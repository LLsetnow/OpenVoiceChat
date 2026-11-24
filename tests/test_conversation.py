"""Tests for the async conversation pipeline."""

import asyncio
from types import SimpleNamespace

from openvoicechat.config import Config
from openvoicechat.conversation import Conversation


class FakeVAD:
    def __call__(self, frame: bytes) -> bool:
        return frame == b"\x01"

    def reset(self) -> None:
        pass


class FakeSpeech:
    def __init__(self, chunks=1):
        self.chunks = chunks
        self.transcribe_calls = 0

    async def transcribe(self, pcm16: bytes) -> str:
        self.transcribe_calls += 1
        return "hello"

    async def synthesize(self, text: str) -> bytes:
        return b"\x02\x00" * 1600 * self.chunks

    async def aclose(self) -> None:
        pass


class FailingSpeech:
    def __init__(self, fail_on_first=True, fail_exc=None):
        self.fail_on_first = fail_on_first
        self.fail_exc = fail_exc or TimeoutError("TTS synthesis timed out")
        self.synthesize_calls = 0
        self.transcribe_calls = 0

    async def transcribe(self, pcm16: bytes) -> str:
        self.transcribe_calls += 1
        return "hello"

    async def synthesize(self, text: str) -> bytes:
        self.synthesize_calls += 1
        if self.fail_exc is not None and (self.fail_on_first or self.synthesize_calls > 1):
            raise self.fail_exc
        return b"\x02\x00" * 1600

    async def aclose(self) -> None:
        pass


class FakeCompletions:
    def __init__(self, tokens=("Hi ", "there.")):
        self.tokens = tokens

    async def create(self, **kwargs):
        async def stream():
            for token in self.tokens:
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content=token))]
                )

        return stream()


class FakeClient:
    def __init__(self, tokens=("Hi ", "there.")):
        self.chat = SimpleNamespace(completions=FakeCompletions(tokens=tokens))


def make_config() -> Config:
    return Config(
        endpoint_silence_ms=64,
        min_speech_ms=32,
        pre_roll_ms=0,
        max_utterance_ms=5000,
        output_chunk_ms=100,
    )


def make_conversation(events, speech=None, on_event=None, client=None) -> Conversation:
    return Conversation(
        speech or FakeSpeech(),
        system_prompt="You are helpful.",
        config=make_config(),
        client=client or FakeClient(),
        on_turn=lambda role, text: events.append((role, text)),
        on_event=on_event,
        vad=FakeVAD(),
    )


async def feed_utterance(conv: Conversation) -> asyncio.Task:
    task = asyncio.create_task(conv.run())
    for frame in (b"\x01", b"\x01", b"\x00", b"\x00"):
        await conv.input.put(frame)
    return task


async def wait_for_role(events, role: str, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not any(event_role == role for event_role, _ in events):
        if loop.time() > deadline:
            raise AssertionError(f"timed out waiting for {role!r} event; got {events!r}")
        await asyncio.sleep(0.005)


async def stop(conv: Conversation, run_task: asyncio.Task) -> None:
    conv.stop()
    await asyncio.wait_for(run_task, timeout=2)


async def test_conversation_produces_audio_and_turns():
    events = []
    conv = make_conversation(events)
    run_task = await feed_utterance(conv)

    chunk = await asyncio.wait_for(conv.output.get(), timeout=2)
    assert len(chunk) == 3200
    assert chunk == b"\x02\x00" * 1600

    await wait_for_role(events, "assistant")
    assert ("user", "hello") in events
    assert ("assistant", "Hi there.") in events

    await stop(conv, run_task)


async def test_history_ends_with_user_and_assistant():
    events = []
    conv = make_conversation(events)
    run_task = await feed_utterance(conv)

    await wait_for_role(events, "assistant")
    assert conv.messages[0] == {"role": "system", "content": "You are helpful."}
    assert [message["role"] for message in conv.messages[-2:]] == ["user", "assistant"]

    await stop(conv, run_task)


async def test_max_utterance_waits_for_silence_instead_of_self_interrupting():
    events = []
    speech = FakeSpeech()
    conv = Conversation(
        speech,
        config=Config(
            endpoint_silence_ms=64,
            min_speech_ms=32,
            pre_roll_ms=0,
            max_utterance_ms=96,
            output_chunk_ms=100,
        ),
        client=FakeClient(),
        on_turn=lambda role, text: events.append((role, text)),
        vad=FakeVAD(),
    )
    run_task = asyncio.create_task(conv.run())
    for _ in range(5):  # keep talking past the 96 ms cap
        await conv.input.put(b"\x01")
    for _ in range(2):  # then stop
        await conv.input.put(b"\x00")

    await wait_for_role(events, "assistant")
    assert speech.transcribe_calls == 1

    await stop(conv, run_task)


async def test_interrupt_cancels_turn_and_drains_output():
    events = []
    conv = make_conversation(events)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def long_turn() -> None:
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    conv._turn_task = asyncio.create_task(long_turn())
    await started.wait()
    await conv.output.put(b"stale audio")

    await conv._interrupt()

    assert cancelled.is_set()
    assert conv._turn_task is None
    assert conv.output.empty()
    assert conv.agent_speaking is False


async def test_send_text_produces_audio_and_turn():
    events = []
    conv = make_conversation(events)
    run_task = asyncio.create_task(conv.run())

    await conv.send_text("Hello from text")
    chunk = await asyncio.wait_for(conv.output.get(), timeout=2)
    assert len(chunk) == 3200

    await wait_for_role(events, "assistant")
    assert ("user", "Hello from text") in events
    assert ("assistant", "Hi there.") in events

    await stop(conv, run_task)


async def test_context_budget_and_summarization_with_long_history():
    events = []
    captured_summaries = []

    # Create 30 turns of history
    history = []
    for i in range(15):
        history.append(
            {
                "role": "user",
                "content": f"第{i + 1}次提问：关于用户喜欢千夏以及去杭州的关键信息{i + 1}",
            }
        )
        history.append(
            {"role": "assistant", "content": f"第{i + 1}次回合回复：收到了你的第{i + 1}次提问"}
        )

    config = Config(
        context_token_budget=200,
        summary_token_budget=100,
        endpoint_silence_ms=64,
        min_speech_ms=32,
        pre_roll_ms=0,
    )
    conv = Conversation(
        FakeSpeech(),
        system_prompt="你是一个贴心的助理。",
        history=history,
        config=config,
        client=FakeClient(),
        on_turn=lambda role, text: events.append((role, text)),
        on_summary=lambda text: captured_summaries.append(text),
        vad=FakeVAD(),
    )

    # Check that summary was generated and captured
    assert len(captured_summaries) > 0
    latest_summary = captured_summaries[-1]
    assert "用户提到" in latest_summary
    assert "关键信息" in latest_summary

    # Total tokens in messages must be <= context_token_budget
    from openvoicechat.conversation import estimate_messages_tokens

    total_tokens = estimate_messages_tokens(conv.messages)
    assert total_tokens <= config.context_token_budget

    # First message is system prompt
    assert conv.messages[0] == {"role": "system", "content": "你是一个贴心的助理。"}
    # Second message is summary of key facts
    assert conv.messages[1]["role"] == "system"
    assert "前情提要" in conv.messages[1]["content"]
    assert "关键信息" in conv.messages[1]["content"]

    # Recent turns are preserved at the end
    assert conv.messages[-1]["role"] == "assistant"
    assert conv.messages[-1]["content"] == "第15次回合回复：收到了你的第15次提问"


async def test_resume_conversation_with_existing_summary():
    events = []
    history = [
        {"role": "user", "content": "现在几点了？"},
        {"role": "assistant", "content": "现在是中午十二点。"},
    ]
    existing_summary = "- 用户提到：喜欢千夏并计划前往杭州\n- 助手确认：已记录行程"

    config = Config(context_token_budget=500)
    conv = Conversation(
        FakeSpeech(),
        system_prompt="你是一个贴心的助理。",
        history=history,
        summary=existing_summary,
        config=config,
        client=FakeClient(),
        on_turn=lambda role, text: events.append((role, text)),
        vad=FakeVAD(),
    )

    assert conv.messages[0] == {"role": "system", "content": "你是一个贴心的助理。"}
    assert conv.messages[1]["role"] == "system"
    assert "喜欢千夏并计划前往杭州" in conv.messages[1]["content"]
    assert conv.messages[-2] == {"role": "user", "content": "现在几点了？"}
    assert conv.messages[-1] == {"role": "assistant", "content": "现在是中午十二点。"}


async def test_tts_timeout_preserves_text_and_context_and_allows_next_turn():
    events = []
    recorded_events = []
    speech = FailingSpeech(fail_on_first=True, fail_exc=TimeoutError("TTS synthesis timed out"))
    conv = make_conversation(
        events,
        speech=speech,
        on_event=lambda kind, **data: recorded_events.append((kind, data)),
    )
    run_task = asyncio.create_task(conv.run())

    await conv.send_text("Hello with TTS timeout")
    await wait_for_role(events, "assistant")

    assert ("user", "Hello with TTS timeout") in events
    assert ("assistant", "Hi there.") in events
    assert conv.messages[-1] == {"role": "assistant", "content": "Hi there."}

    tts_errors = [data for kind, data in recorded_events if kind == "tts_error"]
    assert len(tts_errors) >= 1
    assert "TTS synthesis timed out" in tts_errors[0]["error"]

    end_marker = await asyncio.wait_for(conv.output.get(), timeout=2)
    assert end_marker is None

    assert conv.agent_speaking is False
    assert conv.input_paused is False

    # Second turn succeeds normally
    speech.fail_exc = None
    await conv.send_text("How are you?")
    chunk = await asyncio.wait_for(conv.output.get(), timeout=2)
    assert len(chunk) == 3200

    while len([e for e in events if e[0] == "assistant"]) < 2:
        await asyncio.sleep(0.005)

    assert len(conv.messages) == 5
    assert [m["role"] for m in conv.messages] == [
        "system",
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert conv.messages[2]["content"] == "Hi there."
    assert conv.messages[4]["content"] == "Hi there."

    await stop(conv, run_task)


async def test_tts_failure_after_partial_audio_preserves_full_reply():
    events = []
    recorded_events = []
    speech = FailingSpeech(fail_on_first=False, fail_exc=RuntimeError("TTS second chunk failed"))
    client = FakeClient(tokens=("First sentence. ", "Second sentence."))
    conv = make_conversation(
        events,
        speech=speech,
        client=client,
        on_event=lambda kind, **data: recorded_events.append((kind, data)),
    )
    run_task = asyncio.create_task(conv.run())

    await conv.send_text("Multi-sentence turn")
    first_chunk = await asyncio.wait_for(conv.output.get(), timeout=2)
    assert len(first_chunk) == 3200

    await wait_for_role(events, "assistant")
    assert ("assistant", "First sentence. Second sentence.") in events
    assert conv.messages[-1]["content"] == "First sentence. Second sentence."

    end_marker = await asyncio.wait_for(conv.output.get(), timeout=2)
    assert end_marker is None

    tts_errors = [data for kind, data in recorded_events if kind == "tts_error"]
    assert len(tts_errors) >= 1
    assert "TTS second chunk failed" in tts_errors[0]["error"]

    await stop(conv, run_task)


async def test_retry_speak_replays_speech_without_duplicating_history():
    events = []
    recorded_events = []
    speech = FailingSpeech(fail_on_first=True, fail_exc=TimeoutError("Initial timeout"))
    conv = make_conversation(
        events,
        speech=speech,
        on_event=lambda kind, **data: recorded_events.append((kind, data)),
    )
    run_task = asyncio.create_task(conv.run())

    await conv.send_text("Please say hi")
    await wait_for_role(events, "assistant")

    msg_count_before = len(conv.messages)
    assistant_events_before = [e for e in events if e[0] == "assistant"]
    assert len(assistant_events_before) == 1

    end_marker = await asyncio.wait_for(conv.output.get(), timeout=2)
    assert end_marker is None

    # Now retry speaking: speech will succeed
    speech.fail_exc = None
    speech.synthesize_calls = 0
    await conv.retry_speak()

    chunk = await asyncio.wait_for(conv.output.get(), timeout=2)
    assert len(chunk) == 3200
    end_marker2 = await asyncio.wait_for(conv.output.get(), timeout=2)
    assert end_marker2 is None

    # History and events must not be duplicated
    assert len(conv.messages) == msg_count_before
    assistant_events_after = [e for e in events if e[0] == "assistant"]
    assert len(assistant_events_after) == 1

    # Test explicit text retry
    await conv.retry_speak("Replay this specific sentence.")
    chunk2 = await asyncio.wait_for(conv.output.get(), timeout=2)
    assert len(chunk2) == 3200
    assert len(conv.messages) == msg_count_before
    assert len([e for e in events if e[0] == "assistant"]) == 1

    await stop(conv, run_task)


async def test_saved_conversation_history_persistence_on_tts_failure_and_retry(tmp_path, monkeypatch):
    from openvoicechat import history
    from openvoicechat import migrate as history_migrations

    database_dir = tmp_path / "conv-db"
    database = database_dir / "conversations.sqlite3"
    monkeypatch.setattr(history, "DATA_DIR", database_dir)
    monkeypatch.setattr(history, "DATABASE", database)
    monkeypatch.setattr(history_migrations, "DATA_DIR", database_dir)
    monkeypatch.setattr(history_migrations, "DATABASE", database)
    history_migrations.migrate()

    saved = history.create_conversation("mio", "千夏")
    conv_id = saved["id"]

    events = []
    speech = FailingSpeech(fail_on_first=True, fail_exc=TimeoutError("TTS timeout"))

    def on_turn(role, text):
        events.append((role, text))
        history.append_message(conv_id, role, text)

    conv = Conversation(
        speech,
        system_prompt="You are helpful.",
        config=make_config(),
        client=FakeClient(),
        on_turn=on_turn,
        vad=FakeVAD(),
    )
    run_task = asyncio.create_task(conv.run())

    await conv.send_text("Hello database")
    await wait_for_role(events, "assistant")

    resumed = history.get_conversation(conv_id)
    assert resumed is not None
    assert [(m["role"], m["content"]) for m in resumed["messages"]] == [
        ("user", "Hello database"),
        ("assistant", "Hi there."),
    ]

    # Now retry speaking: history remains single user + single assistant
    speech.fail_exc = None
    await conv.retry_speak()
    chunk = await asyncio.wait_for(conv.output.get(), timeout=2)
    assert len(chunk) == 3200

    resumed_after = history.get_conversation(conv_id)
    assert len(resumed_after["messages"]) == 2

    await stop(conv, run_task)
