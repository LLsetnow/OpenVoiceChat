"""The async listen -> transcribe -> LLM -> speak pipeline."""

import asyncio
import contextlib
import logging
import math
import re
import time
import uuid
from collections import deque

import pysbd
from openai import AsyncOpenAI

from .config import Config
from .vad import VAD

log = logging.getLogger(__name__)
SENTENCE_END_CJK = re.compile(r"[。！？]+[”’」』）】]*")
CLAUSE_END = re.compile(r"[，；：]|[,;:](?=\s)")
EARLIER_SENTENCE_END = re.compile(r"[。！？.!?\n]")
HAN_CHARACTER = re.compile(r"[\u3400-\u9fff]")
LATIN_WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")
CJK_CHAR = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def estimate_tokens(text: str) -> int:
    """Fast heuristic token estimator for multilingual Chinese and English text."""
    if not text:
        return 0
    if not isinstance(text, str):
        text = str(text)
    cjk_count = len(CJK_CHAR.findall(text))
    non_cjk = CJK_CHAR.sub(" ", text)
    words = len(non_cjk.split())
    return int(cjk_count * 1.3) + int(words * 1.2) + 4


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Calculate total estimated tokens for a list of message dictionaries."""
    return sum(estimate_tokens(m.get("content", "")) + 4 for m in messages)


def _summarize_turns(
    overflow_turns: list[dict], existing_summary: str = "", max_tokens: int = 512
) -> str:
    """Extract key facts from older turns and merge with existing summary."""
    points: list[str] = []
    if existing_summary and existing_summary.strip():
        for line in existing_summary.strip().splitlines():
            cleaned = line.strip().lstrip("•-* \t")
            if cleaned and not cleaned.startswith("【"):
                points.append(cleaned)

    for turn in overflow_turns:
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        if len(content) > 100:
            first_clause = content.split("。")[0].split(".")[0].strip()
            snippet = first_clause if len(first_clause) >= 10 else content[:100] + "…"
        else:
            snippet = content
        prefix = "用户提到" if role == "user" else "助手说明"
        fact = f"{prefix}：{snippet}"
        if fact not in points:
            points.append(fact)

    formatted_lines = [f"- {p}" for p in points]
    summary_text = "\n".join(formatted_lines)
    while formatted_lines and estimate_tokens(summary_text) > max_tokens:
        formatted_lines.pop(0)
        summary_text = "\n".join(formatted_lines)
    return summary_text


def _compress_summary(summary: str, max_tokens: int = 512) -> str:
    """Compress summary text to stay within max_tokens."""
    lines = [line for line in summary.strip().splitlines() if line.strip()]
    while lines and estimate_tokens("\n".join(lines)) > max_tokens:
        lines.pop(0)
    return "\n".join(lines)


class Conversation:
    """Owns one voice conversation over input/output PCM16 queues."""

    def __init__(
        self,
        speech,
        *,
        system_prompt: str = "",
        history: list[dict[str, str]] | None = None,
        summary: str | None = None,
        config: Config | None = None,
        client: AsyncOpenAI | None = None,
        on_turn=None,
        on_summary=None,
        on_event=None,
        vad=None,
        verbose: bool = False,
        speaker_mode: bool = False,
    ):
        self.speech = speech
        self.config = config or Config()
        self.system_prompt = system_prompt
        self.summary = summary or ""
        self.on_turn = on_turn
        self.on_summary = on_summary
        self.on_event = on_event
        self.verbose = verbose
        self.speaker_mode = speaker_mode
        self.input_paused = False
        self._owns_client = client is None
        if client is None:
            if not self.config.llm_api_key:
                raise ValueError("LLM_API_KEY (or OPENAI_API_KEY) is required")
            client = AsyncOpenAI(
                api_key=self.config.llm_api_key,
                base_url=self.config.llm_base_url or None,
                timeout=self.config.llm_timeout_s,
                max_retries=1,
            )
        self.client = client
        self.vad = vad or VAD(self.config.vad_threshold, self.config.sample_rate)

        self.input: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=200)
        self.output: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=100)
        self.agent_speaking = False
        self._current_turn_id: str | None = None
        self.messages = [{"role": "system", "content": system_prompt}] if system_prompt else []
        self.messages.extend(
            {"role": item["role"], "content": item["content"]}
            for item in (history or [])
            if item.get("role") in {"user", "assistant"}
            and isinstance(item.get("content"), str)
            and item["content"].strip()
        )
        self._trim_history()
        self._frame_ms = 1000 * self.config.frame_samples / self.config.sample_rate
        self._chunk_bytes = int(self.config.sample_rate * self.config.output_chunk_ms / 1000) * 2
        self._stop = asyncio.Event()
        self._turn_idle_since: float | None = None
        self._need_reset = False
        self._turn_task: asyncio.Task | None = None
        self._asr_task: asyncio.Task[str] | None = None

    async def run(self) -> None:
        pre_roll: deque[bytes] = deque(maxlen=math.ceil(self.config.pre_roll_ms / self._frame_ms))
        onset: list[bytes] = []
        utterance: deque[bytes] = deque()
        utterance_frames = 0
        voiced_run = 0
        in_speech = False
        capped = False
        speech_ms = silence_ms = 0.0
        endpoint_ms = self.config.endpoint_silence_ms
        min_speech_ms = self.config.min_speech_ms
        max_utterance_ms = self.config.max_utterance_ms
        transcribe_stream = getattr(self.speech, "transcribe_stream", None)
        asr_frames: asyncio.Queue[bytes | None] | None = None
        was_paused = False

        def reset() -> None:
            nonlocal in_speech, voiced_run, capped, speech_ms, silence_ms, utterance_frames
            in_speech = False
            voiced_run = 0
            capped = False
            speech_ms = silence_ms = 0.0
            utterance_frames = 0
            utterance.clear()
            onset.clear()
            pre_roll.clear()
            self.vad.reset()

        try:
            while not self._stop.is_set():
                frame = await self.input.get()
                if frame is None or self._stop.is_set():
                    break
                if self._need_reset:
                    self._need_reset = False
                    if asr_frames is not None:
                        await self._close_asr()
                        asr_frames = None
                    reset()
                if not frame:
                    continue
                if self._turn_task is not None and self._turn_task.done():
                    self._turn_task = None
                    self._turn_idle_since = time.monotonic()
                    if self.output.empty() and not self.input_paused:
                        self.agent_speaking = False

                if self.input_paused:
                    if self._turn_idle_since and (time.monotonic() - self._turn_idle_since > 12.0):
                        self._turn_idle_since = None
                        self.resume_input()
                    else:
                        if not was_paused:
                            reset()
                        was_paused = True
                        continue
                was_paused = False
                self._turn_idle_since = None

                voiced = bool(self.vad(frame))

                if not in_speech:
                    if voiced:
                        voiced_run += 1
                        onset.append(frame)
                        if voiced_run >= 2:
                            in_speech = True
                            if self._turn_task is not None or self.agent_speaking:
                                if self.speaker_mode:
                                    reset()
                                    continue
                                await self._interrupt()
                            speech_ms = voiced_run * self._frame_ms
                            pre_roll_ms = len(pre_roll) * self._frame_ms
                            utterance.extend(pre_roll)
                            utterance.extend(onset)
                            utterance_frames = len(pre_roll) + len(onset)
                            pre_roll.clear()
                            onset.clear()
                            self._event("speech_start")
                            if transcribe_stream is not None:
                                asr_frames = asyncio.Queue()
                                for audio in utterance:
                                    asr_frames.put_nowait(audio)
                                self._asr_task = asyncio.create_task(
                                    transcribe_stream(_read_frames(asr_frames))
                                )
                            self._log(f"speech start | pre-roll={pre_roll_ms:.0f}ms")
                    else:
                        voiced_run = 0
                        onset.clear()
                        if pre_roll.maxlen:
                            pre_roll.append(frame)
                    continue

                utterance.append(frame)
                if (
                    asr_frames is not None
                    and self._asr_task is not None
                    and not self._asr_task.done()
                ):
                    asr_frames.put_nowait(frame)
                utterance_frames += 1
                utterance_ms = utterance_frames * self._frame_ms

                if voiced:
                    speech_ms += self._frame_ms
                    silence_ms = 0.0
                    if utterance_ms >= max_utterance_ms:
                        # Still talking past the cap: keep the most recent window and
                        # wait for silence. Ending the turn mid-speech makes the next
                        # voiced frame look like barge-in and cancels it.
                        while utterance_frames > 1 and utterance_ms > max_utterance_ms:
                            utterance.popleft()
                            utterance_frames -= 1
                            utterance_ms = utterance_frames * self._frame_ms
                        if not capped:
                            capped = True
                            if asr_frames is not None:
                                await self._close_asr()
                                asr_frames = None
                            self._log(f"utterance capped | keeping last {utterance_ms:.0f}ms")
                    continue

                silence_ms += self._frame_ms
                if silence_ms < endpoint_ms and utterance_ms < max_utterance_ms:
                    continue

                reason = "max-utterance" if utterance_ms >= max_utterance_ms else "silence"
                self._log(
                    f"endpoint ({reason}) | silence={silence_ms:.0f}ms "
                    f"utterance={utterance_ms:.0f}ms speech={speech_ms:.0f}ms"
                )
                if speech_ms >= min_speech_ms and utterance:
                    self._event("endpoint")
                    if asr_frames is not None:
                        asr_frames.put_nowait(None)
                    self._turn_task = asyncio.create_task(
                        self._turn(b"".join(utterance), self._asr_task)
                    )
                else:
                    await self._close_asr()
                    self._log(f"drop short utterance | speech={speech_ms:.0f}ms")
                    self._event("no_speech")
                asr_frames = None
                reset()
                if pre_roll.maxlen:
                    pre_roll.append(frame)
        finally:
            self._stop.set()
            task, self._turn_task = self._turn_task, None
            if task is not None:
                if not task.done() and not task.cancelling():
                    task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            await self._close_asr()
            self.agent_speaking = False
            _drain(self.output)
            if self._owns_client:
                with contextlib.suppress(Exception):
                    await self.client.close()

    def stop(self) -> None:
        self._stop.set()
        if self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()
        _drain(self.output)
        with contextlib.suppress(asyncio.QueueFull):
            self.input.put_nowait(None)

    def resume_input(self) -> None:
        """Resume recognition after the local speaker has drained its last reply."""
        self.agent_speaking = False
        self.input_paused = False
        _drain(self.input)
        if not self._stop.is_set():
            self._event("mic_paused", paused=False)

    def notify_playback_ended(self) -> None:
        """Called when browser finishes audio playback."""
        self.agent_speaking = False
        _drain(self.input)
        self._need_reset = True
        with contextlib.suppress(asyncio.QueueFull):
            self.input.put_nowait(b"")
        self.resume_input()

    async def send_text(self, text: str) -> None:
        """Process a text turn from the user."""
        text = text.strip()
        if not text or self._stop.is_set():
            return
        if self._turn_task is not None or self.agent_speaking:
            await self._interrupt()
        _drain(self.input)
        self._need_reset = True
        self._turn_task = asyncio.create_task(self._turn(text=text))

    async def retry_speak(self, text: str | None = None) -> None:
        """Re-synthesize and play speech for an existing reply without calling LLM or adding to history."""
        if self._stop.is_set():
            return
        if text is None:
            for message in reversed(self.messages):
                if message.get("role") == "assistant" and isinstance(message.get("content"), str):
                    candidate = message["content"].strip()
                    if candidate:
                        text = candidate
                        break
        if not text or not text.strip():
            return
        text = text.strip()

        if self._turn_task is not None or self.agent_speaking:
            await self._interrupt()
        _drain(self.input)
        self._need_reset = True
        self._turn_task = asyncio.create_task(self._speak_reply(text))

    async def speak_text(self, text: str) -> None:
        """Alias for retry_speak with explicit text."""
        await self.retry_speak(text)

    async def _speak_reply(self, text: str) -> None:
        """Synthesize and stream audio chunks for an existing reply text."""
        turn_id = uuid.uuid4().hex[:8]
        self._current_turn_id = turn_id
        self._event("turn_start", turn_id=turn_id, input_type="retry_speak")
        spoken: list[str] = []
        sentences: asyncio.Queue[str | None] = asyncio.Queue(maxsize=16)
        tts_task = asyncio.create_task(self._speak(sentences, spoken, 0.0, turn_id=turn_id))
        tts_error: Exception | None = None
        try:
            parts = _split_sentences(text)
            for part in parts:
                await self._feed(sentences, part, tts_task)
            await self._feed(sentences, None, tts_task)
            await tts_task
        except asyncio.CancelledError:
            self._log(f"retry_speak cancelled | spoken={len(spoken)}")
            self.agent_speaking = False
            self.input_paused = False
            self._event("mic_paused", paused=False)
            self._event("interrupted")
            raise
        except Exception as exc:
            tts_error = exc
            log.exception("retry_speak failed")
            self._log(f"retry_speak error: {exc!r}")
            self.agent_speaking = False
            self.input_paused = False
            self._event("mic_paused", paused=False)
            self._event("tts_error", error=str(exc), message_key="tts_error_notice")
        finally:
            if tts_task is not None and not tts_task.done():
                tts_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await tts_task
            if not spoken or tts_error is not None:
                self.agent_speaking = False
                self.input_paused = False
                self._event("mic_paused", paused=False)
            if not self._stop.is_set():
                await self.output.put(None)
            with contextlib.suppress(asyncio.QueueFull):
                self.input.put_nowait(b"")

    async def _turn(
        self,
        pcm: bytes = b"",
        asr_task: asyncio.Task[str] | None = None,
        *,
        text: str | None = None,
    ) -> None:
        turn_id = uuid.uuid4().hex[:8]
        self._current_turn_id = turn_id
        self._event("turn_start", turn_id=turn_id, input_type="voice" if text is None else "text")
        spoken: list[str] = []
        sentences: asyncio.Queue[str | None] = asyncio.Queue(maxsize=16)
        tts_task: asyncio.Task | None = None
        response = ""
        stream = None
        completed = False
        started = time.monotonic()
        if text is not None:
            self._log(f"turn start | text={text!r}")
        else:
            self._log(f"turn start | pcm={len(pcm) / 2 / self.config.sample_rate * 1000:.0f}ms")
        try:
            if text is None:
                stt_started = time.monotonic()
                text = await asr_task if asr_task is not None else await self.speech.transcribe(pcm)
                text = text.strip()
                asr_ms = round(1000 * (time.monotonic() - stt_started))
                self._log(f"STT ({asr_ms}ms): {text!r}")
                self._event("timing", stage="asr", milliseconds=asr_ms, turn_id=turn_id)
            else:
                text = text.strip()
                self._event("timing", stage="asr", milliseconds=0, turn_id=turn_id)
            if not text:
                self.agent_speaking = False
                self.input_paused = False
                self._event("mic_paused", paused=False)
                self._event("no_speech")
                return
            self.messages.append({"role": "user", "content": text})
            self._trim_history()
            self._emit("user", text)

            segmenter = pysbd.Segmenter(language="en", clean=False)
            buffer = ""
            llm_started = time.monotonic()
            tts_task = asyncio.create_task(self._speak(sentences, spoken, llm_started, turn_id=turn_id))
            first_token = True
            first_clause = self.config.speak_first_clause
            request_options = (
                {"extra_body": self.config.llm_extra_body} if self.config.llm_extra_body else {}
            )
            stream = await self.client.chat.completions.create(
                model=self.config.llm_model,
                messages=self.messages,
                stream=True,
                **request_options,
            )
            async for chunk in stream:
                for choice in getattr(chunk, "choices", None) or []:
                    token = getattr(getattr(choice, "delta", None), "content", None)
                    if not token:
                        continue
                    if first_token:
                        first_token = False
                        first_ms = round(1000 * (time.monotonic() - llm_started))
                        self._log(f"LLM first token ({first_ms}ms)")
                        self._event("timing", stage="llm_first", milliseconds=first_ms, turn_id=turn_id)
                    response += token
                    self._event("assistant_partial", text=response)
                    buffer += token
                    if first_clause and (part := _take_first_clause(buffer)) is not None:
                        text, buffer = part
                        await self._feed(sentences, text, tts_task)
                        first_clause = False
                    sentence_start = 0
                    for boundary in SENTENCE_END_CJK.finditer(buffer):
                        sentence = buffer[sentence_start : boundary.end()].strip()
                        if sentence:
                            await self._feed(sentences, sentence, tts_task)
                            first_clause = False
                        sentence_start = boundary.end()
                    buffer = buffer[sentence_start:]
                    if any(mark in buffer for mark in ".!?\n"):
                        parts = segmenter.segment(buffer)
                        if len(parts) > 1:
                            for sentence in parts[:-1]:
                                sentence = sentence.strip()
                                if sentence:
                                    await self._feed(sentences, sentence, tts_task)
                                    first_clause = False
                            buffer = parts[-1]

            if buffer.strip():
                await self._feed(sentences, buffer.strip(), tts_task)
            await self._feed(sentences, None, tts_task)
            if response.strip():
                self.messages.append({"role": "assistant", "content": response})
                self._emit("assistant", response)
                completed = True
            else:
                self._event("empty_reply")

            tts_error: Exception | None = None
            try:
                await tts_task
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                tts_error = exc
                log.exception("tts synthesis failed")

            if not spoken or tts_error is not None:
                self.agent_speaking = False
                self.input_paused = False
                self._event("mic_paused", paused=False)

            if tts_error is not None:
                self._log(f"TTS error: {tts_error!r}")
                self._event("tts_error", error=str(tts_error), message_key="tts_error_notice")
        except asyncio.CancelledError:
            self._log(f"turn cancelled | spoken_sentences={len(spoken)}")
            partial = " ".join(spoken).strip()
            if partial and not completed:
                self.messages.append({"role": "assistant", "content": partial})
                self._emit("assistant", partial)
            self.agent_speaking = False
            self.input_paused = False
            self._event("mic_paused", paused=False)
            self._event("interrupted")
            raise
        except Exception as exc:
            self._log(f"turn error: {exc!r}")
            log.exception("conversation turn failed")
            partial = " ".join(spoken).strip()
            if partial and not completed:
                self.messages.append({"role": "assistant", "content": partial})
                self._emit("assistant", partial)
            self.agent_speaking = False
            self.input_paused = False
            self._event("mic_paused", paused=False)
            self._emit("error", str(exc))
        finally:
            self._log(f"turn finished ({1000 * (time.monotonic() - started):.0f}ms)")
            self._trim_history()
            if tts_task is not None:
                if not tts_task.done():
                    tts_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await tts_task
            if asr_task is not None and self._asr_task is asr_task:
                await self._close_asr()
            close_stream = getattr(stream, "close", None)
            if close_stream is not None:
                with contextlib.suppress(Exception):
                    await close_stream()
            if not self._stop.is_set():
                # Ordered after all PCM in this reply, including pauses between sentences.
                await self.output.put(None)
            with contextlib.suppress(asyncio.QueueFull):
                self.input.put_nowait(b"")

    async def _speak(
        self,
        sentences: asyncio.Queue,
        spoken: list[str],
        llm_started: float = 0.0,
        turn_id: str | None = None,
    ) -> None:
        first_segment = True
        while True:
            sentence = await sentences.get()
            if sentence is None:
                return
            tts_started = time.monotonic()
            if first_segment:
                if llm_started > 0:
                    self._event(
                        "timing",
                        stage="llm",
                        milliseconds=round(1000 * (tts_started - llm_started)),
                        turn_id=turn_id,
                    )
                self._event("tts_start", turn_id=turn_id)
            audio = await self.speech.synthesize(sentence)
            tts_ms = round(1000 * (time.monotonic() - tts_started))
            if first_segment:
                self._event("timing", stage="tts", milliseconds=tts_ms, turn_id=turn_id)
                self._event("first_audio_ready", turn_id=turn_id, tts_ms=tts_ms)
                first_segment = False
            self._log(f"TTS ({tts_ms}ms, {len(audio)} bytes): {sentence!r}")
            if not audio:
                continue
            if self.speaker_mode and not self.input_paused:
                self.input_paused = True
                self._event("mic_paused", paused=True)
            spoken.append(sentence)
            self.agent_speaking = True
            for start in range(0, len(audio), self._chunk_bytes):
                await self.output.put(audio[start : start + self._chunk_bytes])

    @staticmethod
    async def _feed(queue: asyncio.Queue, item, tts_task: asyncio.Task) -> bool:
        if tts_task.done():
            return False
        put = asyncio.create_task(queue.put(item))
        try:
            done, _ = await asyncio.wait((put, tts_task), return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            put.cancel()
            await asyncio.gather(put, return_exceptions=True)
            raise
        if put in done:
            put.result()
            return True
        put.cancel()
        await asyncio.gather(put, return_exceptions=True)
        return False

    async def _interrupt(self) -> None:
        t0 = time.monotonic()
        self._log(f"barge-in | cancelling turn, output_chunks={self.output.qsize()}")
        task, self._turn_task = self._turn_task, None
        self.agent_speaking = False
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._close_asr()
        cleared_chunks = _drain(self.output)
        self.input_paused = False
        self._event("mic_paused", paused=False)
        duration_ms = max(1, round(1000 * (time.monotonic() - t0)))
        self._event(
            "interrupted",
            duration_ms=duration_ms,
            cleared_chunks=cleared_chunks,
            turn_id=self._current_turn_id,
        )

    async def _close_asr(self) -> None:
        task, self._asr_task = self._asr_task, None
        if task is not None:
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    def _log(self, message: str) -> None:
        if self.verbose:
            print(f"[conversation] {message}", flush=True)

    def _emit(self, role: str, text: str) -> None:
        if self.on_turn is None:
            return
        try:
            self.on_turn(role, text)
        except Exception:
            log.exception("on_turn callback failed")

    def _event(self, kind: str, **data) -> None:
        if self.on_event is not None:
            try:
                self.on_event(kind, **data)
            except Exception:
                log.exception("on_event callback failed")

    def _trim_history(self) -> None:
        turns = [
            m
            for m in self.messages
            if m.get("role") in {"user", "assistant"}
            and isinstance(m.get("content"), str)
            and m["content"].strip()
        ]
        budget = getattr(self.config, "context_token_budget", 2048)
        summary_budget = getattr(self.config, "summary_token_budget", 512)
        sys_tokens = estimate_tokens(self.system_prompt) if self.system_prompt else 0
        summary_tokens = (estimate_tokens(self.summary) + 15) if self.summary else 0

        avail = budget - sys_tokens - summary_tokens
        if avail < 100 and self.summary:
            self.summary = _compress_summary(self.summary, max_tokens=summary_budget)
            summary_tokens = (estimate_tokens(self.summary) + 15) if self.summary else 0
            avail = budget - sys_tokens - summary_tokens

        avail = max(40, avail)

        kept_turns: list[dict] = []
        turn_cost = 0
        for t in reversed(turns):
            cost = estimate_tokens(t["content"]) + 4
            if not kept_turns or (turn_cost + cost <= avail):
                kept_turns.insert(0, t)
                turn_cost += cost
            else:
                break

        overflow_turns = turns[: len(turns) - len(kept_turns)]
        if overflow_turns:
            updated_summary = _summarize_turns(
                overflow_turns, self.summary, max_tokens=summary_budget
            )
            if updated_summary != self.summary:
                self.summary = updated_summary
                if self.on_summary is not None:
                    try:
                        self.on_summary(self.summary)
                    except Exception:
                        log.exception("on_summary callback failed")

        def build_messages(k_turns: list[dict], sum_text: str) -> list[dict]:
            res = []
            if self.system_prompt:
                res.append({"role": "system", "content": self.system_prompt})
            if sum_text:
                res.append(
                    {
                        "role": "system",
                        "content": f"【前情提要 / 早期对话关键事实】\n{sum_text}",
                    }
                )
            res.extend(k_turns)
            return res

        rebuilt = build_messages(kept_turns, self.summary)

        # Strictly enforce context_token_budget
        while len(kept_turns) > 1 and estimate_messages_tokens(rebuilt) > budget:
            popped = kept_turns.pop(0)
            overflow_turns.append(popped)
            rebuilt = build_messages(kept_turns, self.summary)

        # If still over budget, compress summary lines
        while estimate_messages_tokens(rebuilt) > budget and self.summary:
            shrunk = _compress_summary(self.summary, max_tokens=max(20, summary_budget - 30))
            if shrunk == self.summary:
                break
            self.summary = shrunk
            summary_budget = max(20, summary_budget - 30)
            rebuilt = build_messages(kept_turns, self.summary)

        self.messages = rebuilt


def _take_first_clause(buffer: str) -> tuple[str, str] | None:
    """Cut only at a usable clause boundary, before any complete sentence."""
    end = EARLIER_SENTENCE_END.search(buffer)
    limit = end.start() if end else len(buffer)
    for boundary in CLAUSE_END.finditer(buffer):
        if boundary.end() > limit:
            break
        prefix = buffer[: boundary.end()].strip()
        if len(HAN_CHARACTER.findall(prefix)) >= 7 or len(LATIN_WORD.findall(prefix)) >= 3:
            return prefix, buffer[boundary.end() :]
    return None


def _drain(queue: asyncio.Queue) -> int:
    count = 0
    while True:
        try:
            queue.get_nowait()
            count += 1
        except asyncio.QueueEmpty:
            return count


async def _read_frames(queue: asyncio.Queue[bytes | None]):
    while (frame := await queue.get()) is not None:
        yield frame


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences using CJK and Western punctuation boundaries."""
    text = text.strip()
    if not text:
        return []
    chunks = []
    start = 0
    for match in SENTENCE_END_CJK.finditer(text):
        sentence = text[start : match.end()].strip()
        if sentence:
            chunks.append(sentence)
        start = match.end()
    remaining = text[start:].strip()
    if remaining:
        chunks.append(remaining)

    segmenter = pysbd.Segmenter(language="en", clean=False)
    result = []
    for chunk in chunks:
        if any(mark in chunk for mark in ".!?\n"):
            sub_parts = segmenter.segment(chunk)
            for part in sub_parts:
                part = part.strip()
                if part:
                    result.append(part)
        else:
            result.append(chunk)
    return result or [text]
