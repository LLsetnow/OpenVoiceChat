"""Paraformer streaming recognition and AiServer GPT-SoVITS synthesis."""

import asyncio
import base64
import io
import json
import os
import re
import uuid
import wave
from collections.abc import AsyncIterable, Callable

import httpx
import numpy as np
import soxr
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from .audio import SPEECH_RATE
from .config import DEFAULT_SOVITS_URL

ASR_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
ASR_MODEL = "qwen-audio-3.1-asr-flash-streaming"
TTS_URL = (os.getenv("SOVITS_URL") or DEFAULT_SOVITS_URL).rstrip("/") + "/"
TTS_VOICE = "chinatsu"
TTS_SAMPLE_STEPS = 8
COSYVOICE_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"


class AliyunSpeechPipeline:
    """Unified pipeline supporting Aliyun Paraformer ASR and multiple TTS backends."""
    def __init__(
        self, api_key: str, *, voice: str, language: str,
        on_partial: Callable[[str], None] | None = None,
        sample_steps: int = TTS_SAMPLE_STEPS,
        tts_engine: str = "gpt_sovits",
        sovits_url: str | None = None,
    ):
        self._api_key = api_key
        self.voice = voice
        self.language = language
        self.on_partial = on_partial
        self.sample_steps = sample_steps
        self.tts_engine = tts_engine
        self.sovits_url = (sovits_url or os.getenv("SOVITS_URL") or DEFAULT_SOVITS_URL).rstrip("/")
        self._tts = httpx.AsyncClient(timeout=45, trust_env=False)

    async def transcribe(self, pcm: bytes) -> str:
        async def frames():
            for offset in range(0, len(pcm), 3200):
                yield pcm[offset : offset + 3200]

        return await self.transcribe_stream(frames())

    async def transcribe_stream(self, frames: AsyncIterable[bytes]) -> str:
        task_id = str(uuid.uuid4())

        def instruction(action: str, **payload) -> str:
            return json.dumps({
                "header": {"action": action, "task_id": task_id, "streaming": "duplex"},
                "payload": {"input": {}, **payload},
            })

        try:
            async with asyncio.timeout(45), connect(
                ASR_URL,
                additional_headers={"Authorization": f"Bearer {self._api_key}"},
                open_timeout=8,
                close_timeout=1,
            ) as socket:
                await socket.send(instruction(
                    "run-task",
                    task_group="audio",
                    task="asr",
                    function="recognition",
                    model=ASR_MODEL,
                    parameters={"format": "pcm", "sample_rate": SPEECH_RATE},
                ))
                async with asyncio.timeout(8):
                    event, _ = await _receive(socket)
                if event != "task-started":
                    raise RuntimeError(f"Unexpected Aliyun ASR startup event: {event}")

                async def send_audio() -> None:
                    buffer = bytearray()
                    async for frame in frames:
                        buffer.extend(frame)
                        if len(buffer) >= 3000:
                            await socket.send(bytes(buffer))
                            buffer.clear()
                    if buffer:
                        await socket.send(bytes(buffer))
                    await socket.send(instruction("finish-task"))

                async def read_text() -> str:
                    sentences = []
                    last_partial = ""
                    pending_sentence = ""
                    while True:
                        event, payload = await _receive(socket)
                        if event == "task-finished":
                            if pending_sentence:
                                sentences.append(pending_sentence)
                            return " ".join(sentences)
                        if event == "result-generated":
                            sentence = payload.get("output", {}).get("sentence", {})
                            text = sentence.get("text", "").strip()
                            if sentence.get("heartbeat") or not text:
                                continue
                            if sentence.get("sentence_end"):
                                sentences.append(text)
                                pending_sentence = ""
                                draft = " ".join(sentences)
                            else:
                                pending_sentence = text
                                draft = " ".join([*sentences, text])
                            if self.on_partial and draft != last_partial:
                                last_partial = draft
                                self.on_partial(draft)

                tasks = (asyncio.create_task(send_audio()), asyncio.create_task(read_text()))
                try:
                    _, text = await asyncio.gather(*tasks)
                    return text
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except InvalidStatus as exc:
            raise RuntimeError(
                f"Aliyun ASR connection rejected (HTTP {exc.response.status_code})"
            ) from None
        except TimeoutError:
            raise RuntimeError("Aliyun ASR timed out") from None

    async def synthesize(self, text: str) -> bytes:
        clean_text = clean_text_for_speech(text)
        if not clean_text:
            return b""
        if self.tts_engine in ("cosyvoice", "dashscope", "aliyun"):
            return await self._synthesize_cosyvoice(clean_text)
        return await self._synthesize_sovits(clean_text)

    async def _synthesize_sovits(self, text: str) -> bytes:
        sovits_endpoint = f"{self.sovits_url}/"
        response = await self._tts.post(sovits_endpoint, json={
            "text": text,
            "voice": self.voice,
            "text_language": self.language,
            "sample_steps": self.sample_steps,
        })
        response.raise_for_status()
        with wave.open(io.BytesIO(response.content), "rb") as wav:
            if wav.getsampwidth() != 2:
                raise ValueError("GPT-SoVITS returned a WAV with an unsupported sample width")
            rate = wav.getframerate()
            channels = wav.getnchannels()
            samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
        if not samples.size:
            return b""
        if channels > 1:
            samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
        if rate != SPEECH_RATE:
            samples = soxr.resample(samples, rate, SPEECH_RATE)
        return samples.tobytes()

    async def _synthesize_cosyvoice(self, text: str) -> bytes:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "X-DashScope-SSE": "enable",
        }
        model = "cosyvoice-v2"
        payload = {
            "text": text,
            "voice": self.voice,
            "format": "pcm",
            "sample_rate": SPEECH_RATE,
        }
        pcm_chunks = bytearray()
        async with self._tts.stream(
            "POST", COSYVOICE_URL, headers=headers, json={"model": model, "input": payload},
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    chunk = json.loads(line[5:])
                except json.JSONDecodeError:
                    continue
                code = chunk.get("code")
                if code and code != "Success":
                    raise RuntimeError(f"CosyVoice TTS failed: {code}")
                audio_b64 = chunk.get("output", {}).get("audio", {}).get("data")
                if audio_b64:
                    pcm_chunks.extend(base64.b64decode(audio_b64))
        return bytes(pcm_chunks)

    async def aclose(self) -> None:
        await self._tts.aclose()


# Backward compatibility alias
AliyunSoVITS = AliyunSpeechPipeline


def clean_text_for_speech(text: str) -> str:
    """Filter out stage directions in parentheses/brackets and emotion tags (<...>) before TTS."""
    clean = re.sub(r"<[^>]+>", "", text)
    clean = re.sub(r"[\(（][^\)）]*[\)）]", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


async def _receive(socket) -> tuple[str, dict]:
    message = json.loads(await socket.recv())
    header = message["header"]
    event = header["event"]
    if event == "task-failed":
        raise RuntimeError(f"Aliyun ASR task failed ({header.get('error_code', 'unknown')})")
    return event, message.get("payload", {})
