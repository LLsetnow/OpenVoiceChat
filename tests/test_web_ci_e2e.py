"""End-to-end regression tests for Web voice sessions, history restore, and failure recovery in CI."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import run_voice_web
from openvoicechat import history
from openvoicechat import migrate as history_migrations
from openvoicechat.audio import SPEECH_FRAME_BYTES
from openvoicechat.config import Config


class MockSpeechPipeline:
    """Mock speech pipeline replacing cloud ASR and TTS."""

    def __init__(
        self,
        transcribe_text: str = "你好千夏",
        audio_chunk: bytes = b"\x02\x00" * 1600,
    ):
        self.transcribe_text = transcribe_text
        self.audio_chunk = audio_chunk
        self.transcribe_calls = 0
        self.synthesize_calls = 0
        self.synthesize_fail_exc: Exception | None = None
        self.fail_on_first_only: bool = True
        self.synthesized_texts: list[str] = []
        self.aclose_called: bool = False

    async def transcribe(self, pcm: bytes) -> str:
        self.transcribe_calls += 1
        return self.transcribe_text

    async def synthesize(self, text: str) -> bytes:
        self.synthesize_calls += 1
        self.synthesized_texts.append(text)
        if self.synthesize_fail_exc is not None and (
            not self.fail_on_first_only or self.synthesize_calls == 1
        ):
            raise self.synthesize_fail_exc
        return self.audio_chunk

    async def aclose(self) -> None:
        self.aclose_called = True


class MockVAD:
    """Mock Silero VAD treating frames starting with 0x01 as speech."""

    def __call__(self, frame: bytes) -> bool:
        return bool(frame and frame[0] == 1)

    def reset(self) -> None:
        pass


class MockCompletions:
    """Mock OpenAI streaming completions."""

    def __init__(self, tokens: tuple[str, ...] = ("你好！", "很高兴见到你。")):
        self.tokens = tokens
        self.call_history: list[dict[str, Any]] = []

    async def create(self, **kwargs):
        self.call_history.append(kwargs)

        async def stream():
            for token in self.tokens:
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content=token))]
                )

        return stream()


class MockLLMClient:
    def __init__(self, tokens: tuple[str, ...] = ("你好！", "很高兴见到你。")):
        self.chat = SimpleNamespace(completions=MockCompletions(tokens=tokens))
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class WebE2EEnvironment:
    def __init__(
        self,
        client: TestClient,
        speech: MockSpeechPipeline,
        llm_client: MockLLMClient,
        data_dir: Path,
        db_file: Path,
    ):
        self.client = client
        self.speech = speech
        self.llm_client = llm_client
        self.data_dir = data_dir
        self.db_file = db_file

    @staticmethod
    def classify_message(raw_msg: dict) -> dict:
        """Classify a raw WebSocket message into kind: audio, partial, text, or raw json."""
        if "bytes" in raw_msg:
            return {"kind": "audio", "bytes": raw_msg["bytes"]}
        if "text" in raw_msg:
            parsed = json.loads(raw_msg["text"])
            kind = parsed.get("kind")
            if kind in ("assistant_partial", "user_partial"):
                return {"kind": "partial", "text": parsed.get("text"), "raw": parsed}
            if kind == "message":
                return {
                    "kind": "text",
                    "role": parsed.get("role"),
                    "text": parsed.get("text"),
                    "raw": parsed,
                }
            return parsed
        return raw_msg

    def receive_messages(self, ws, count: int = 10) -> list[dict]:
        items = []
        for _ in range(count):
            raw = ws.receive()
            items.append(self.classify_message(raw))
        return items

    def wait_for_event(self, ws, kind: str, max_steps: int = 25) -> dict:
        for _ in range(max_steps):
            raw = ws.receive()
            classified = self.classify_message(raw)
            if classified.get("kind") == kind:
                return classified
        raise TimeoutError(f"Did not receive event of kind {kind!r} within {max_steps} messages.")


@pytest.fixture
def web_e2e_env(monkeypatch, tmp_path):
    data_dir = tmp_path / "web_ci_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "conversations.sqlite3"
    migrations_dir = Path(__file__).resolve().parents[1] / "openvoicechat" / "migrations"

    monkeypatch.setattr(history, "DATA_DIR", data_dir)
    monkeypatch.setattr(history, "DATABASE", db_file)
    monkeypatch.setattr(history_migrations, "DATA_DIR", data_dir)
    monkeypatch.setattr(history_migrations, "DATABASE", db_file)
    monkeypatch.setattr(history_migrations, "MIGRATIONS_DIR", migrations_dir)
    history_migrations.migrate()

    run_voice_web.app.state.database_status = None
    run_voice_web.app.state.migration_error = None
    run_voice_web.active_session_stop = None
    if run_voice_web.audio_in_use.locked():
        run_voice_web.audio_in_use.release()

    mock_speech = MockSpeechPipeline()
    mock_llm = MockLLMClient()
    mock_vad = MockVAD()
    fast_config = Config(
        endpoint_silence_ms=40,
        min_speech_ms=20,
        pre_roll_ms=0,
        speak_first_clause=True,
    )

    monkeypatch.setattr(
        run_voice_web,
        "voice_profile",
        lambda **kwargs: (fast_config, mock_speech),
    )

    monkeypatch.setattr(
        run_voice_web,
        "audio_devices",
        lambda refresh=True: {
            "input": {
                "default": "Mock Mic",
                "devices": [{"id": "mock_mic_1", "label": "Mock Mic", "index": 0}],
            },
            "output": {
                "default": "Mock Speaker",
                "devices": [{"id": "mock_spk_1", "label": "Mock Speaker", "index": 1}],
            },
        },
    )

    orig_conv_cls = run_voice_web.Conversation

    def patched_conv(*args, **kwargs):
        kwargs.setdefault("client", mock_llm)
        kwargs.setdefault("vad", mock_vad)
        return orig_conv_cls(*args, **kwargs)

    monkeypatch.setattr(run_voice_web, "Conversation", patched_conv)

    client = TestClient(run_voice_web.app, base_url="http://127.0.0.1")
    env = WebE2EEnvironment(client, mock_speech, mock_llm, data_dir, db_file)

    yield env

    if run_voice_web.audio_in_use.locked():
        run_voice_web.audio_in_use.release()
    run_voice_web.active_session_stop = None


def test_static_assets_and_api_routes(web_e2e_env):
    client = web_e2e_env.client

    resp_index = client.get("/")
    assert resp_index.status_code == 200
    assert "text/html" in resp_index.headers["content-type"]
    assert "OpenVoiceChat" in resp_index.text

    for asset_name in ("voice.js", "voice.css", "i18n.js"):
        resp_asset = client.get(f"/assets/{asset_name}")
        assert resp_asset.status_code == 200, f"Failed loading asset /assets/{asset_name}"

    resp_status = client.get("/api/status")
    assert resp_status.status_code == 200
    assert resp_status.json()["status"] == "ready"

    resp_chars = client.get("/api/characters")
    assert resp_chars.status_code == 200
    data_chars = resp_chars.json()
    assert "characters" in data_chars and "default" in data_chars
    assert any(c["id"] == "example" for c in data_chars["characters"])

    resp_voices = client.get("/api/voices")
    assert resp_voices.status_code == 200
    data_voices = resp_voices.json()
    assert "gpt_sovits" in data_voices
    assert "cosyvoice" in data_voices
    assert len(data_voices["gpt_sovits"]) >= 1

    resp_convs = client.get("/api/conversations")
    assert resp_convs.status_code == 200
    assert resp_convs.json() == {"conversations": []}

    resp_not_found = client.get("/api/conversations/non_existent_conv_id")
    assert resp_not_found.status_code == 404


def test_missing_database_auto_migrates_on_first_request(monkeypatch, tmp_path):
    fresh_dir = tmp_path / "fresh_auto_migrated_db"
    fresh_db = fresh_dir / "conversations.sqlite3"
    migrations_dir = Path(__file__).resolve().parents[1] / "openvoicechat" / "migrations"

    assert not fresh_db.exists()

    monkeypatch.setattr(history, "DATA_DIR", fresh_dir)
    monkeypatch.setattr(history, "DATABASE", fresh_db)
    monkeypatch.setattr(history_migrations, "DATA_DIR", fresh_dir)
    monkeypatch.setattr(history_migrations, "DATABASE", fresh_db)
    monkeypatch.setattr(history_migrations, "MIGRATIONS_DIR", migrations_dir)

    run_voice_web.app.state.database_status = None
    run_voice_web.app.state.migration_error = None

    with TestClient(run_voice_web.app) as client:
        status_resp = client.get("/api/status")
        assert status_resp.status_code == 200
        data = status_resp.json()
        assert data["status"] == "ready"
        assert data["database"]["applied"] == [1, 2]
        assert fresh_db.is_file()

        conv = history.create_conversation("mio", "千夏")
        history.append_message(conv["id"], "user", "自动迁移测试")
        history.append_message(conv["id"], "assistant", "已成功创建数据库")

        conv_resp = client.get(f"/api/conversations/{conv['id']}")
        assert conv_resp.status_code == 200
        assert len(conv_resp.json()["messages"]) == 2


def test_websocket_handshake_security_and_system_actions(web_e2e_env):
    client = web_e2e_env.client

    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(
            "/session", headers={"origin": "http://malicious-attacker.com", "host": "127.0.0.1"}
        ),
    ):
        pass
    assert exc_info.value.code == 1008

    with client.websocket_connect(
        "/session", headers={"origin": "http://127.0.0.1", "host": "127.0.0.1"}
    ) as ws:
        initial_event = ws.receive_json()
        assert initial_event == {"kind": "state", "state": "idle"}

        ws.send_json({"action": "init"})
        chars_event = ws.receive_json()
        assert chars_event["kind"] == "characters"
        assert "characters" in chars_event
        assert chars_event["current"] == "千夏"

        ws.send_json({"action": "devices"})
        devices_event = ws.receive_json()
        assert devices_event["kind"] == "devices"
        assert "input" in devices_event and "output" in devices_event
        chars_event_2 = ws.receive_json()
        assert chars_event_2["kind"] == "characters"


def test_websocket_start_and_text_input_turn_e2e(web_e2e_env):
    client = web_e2e_env.client

    with client.websocket_connect(
        "/session", headers={"origin": "http://127.0.0.1", "host": "127.0.0.1"}
    ) as ws:
        assert ws.receive_json() == {"kind": "state", "state": "idle"}

        ws.send_json({"action": "text", "text": "你好千夏，请介绍一下你自己。"})

        received_audio: list[bytes] = []
        received_partials: list[str] = []
        received_messages: list[dict] = []
        conversation_id = None

        for _ in range(30):
            raw = ws.receive()
            classified = web_e2e_env.classify_message(raw)
            kind = classified.get("kind")
            if kind == "audio":
                received_audio.append(classified["bytes"])
            elif kind == "partial":
                received_partials.append(classified["text"])
            elif kind == "text":
                received_messages.append(classified)
            elif kind == "conversation":
                conversation_id = classified["id"]
            elif kind == "output_complete":
                break

        assert conversation_id is not None
        assert len(received_audio) >= 1
        assert len(received_partials) >= 1
        assert any("你好！" in p for p in received_partials)

        roles = [m["role"] for m in received_messages]
        assert "user" in roles
        assert "assistant" in roles

        user_entry = next(m for m in received_messages if m["role"] == "user")
        assert user_entry["text"] == "你好千夏，请介绍一下你自己。"
        asst_entry = next(m for m in received_messages if m["role"] == "assistant")
        assert asst_entry["text"] == "你好！很高兴见到你。"

        saved_conv = history.get_conversation(conversation_id)
        assert saved_conv is not None
        assert len(saved_conv["messages"]) == 2
        assert saved_conv["messages"][0]["content"] == "你好千夏，请介绍一下你自己。"
        assert saved_conv["messages"][1]["content"] == "你好！很高兴见到你。"


def test_websocket_audio_binary_stream_turn_e2e(web_e2e_env):
    client = web_e2e_env.client

    with client.websocket_connect(
        "/session", headers={"origin": "http://127.0.0.1", "host": "127.0.0.1"}
    ) as ws:
        assert ws.receive_json() == {"kind": "state", "state": "idle"}

        ws.send_json({"action": "start"})

        while True:
            msg = ws.receive_json()
            if msg.get("kind") == "state" and msg.get("state") == "running":
                break

        # Send 2 speech frames (voiced)
        ws.send_bytes(b"\x01" * SPEECH_FRAME_BYTES)
        ws.send_bytes(b"\x01" * SPEECH_FRAME_BYTES)
        # Send 3 silence frames (trigger endpoint silence)
        ws.send_bytes(b"\x00" * SPEECH_FRAME_BYTES)
        ws.send_bytes(b"\x00" * SPEECH_FRAME_BYTES)
        ws.send_bytes(b"\x00" * SPEECH_FRAME_BYTES)

        received_audio: list[bytes] = []
        received_messages: list[dict] = []
        speech_start_received = False
        endpoint_received = False

        for _ in range(35):
            raw = ws.receive()
            classified = web_e2e_env.classify_message(raw)
            kind = classified.get("kind")
            if kind == "audio":
                received_audio.append(classified["bytes"])
            elif kind == "text":
                received_messages.append(classified)
            elif kind == "speech_start":
                speech_start_received = True
            elif kind == "endpoint":
                endpoint_received = True
            elif kind == "output_complete":
                break

        assert speech_start_received is True
        assert endpoint_received is True
        assert len(received_audio) >= 1

        user_entry = next((m for m in received_messages if m["role"] == "user"), None)
        assert user_entry is not None
        assert user_entry["text"] == "你好千夏"

        asst_entry = next((m for m in received_messages if m["role"] == "assistant"), None)
        assert asst_entry is not None
        assert asst_entry["text"] == "你好！很高兴见到你。"


def test_stop_action_and_graceful_idle_transition(web_e2e_env):
    client = web_e2e_env.client

    with client.websocket_connect(
        "/session", headers={"origin": "http://127.0.0.1", "host": "127.0.0.1"}
    ) as ws:
        ws.receive_json()
        ws.send_json({"action": "start"})

        while True:
            msg = ws.receive_json()
            if msg.get("kind") == "state" and msg.get("state") == "running":
                break

        assert run_voice_web.audio_in_use.locked() is True
        assert run_voice_web.active_session_stop is not None

        ws.send_json({"action": "stop"})

        idle_received = False
        for _ in range(10):
            msg = ws.receive_json()
            if msg.get("kind") == "state" and msg.get("state") == "idle":
                idle_received = True
                break

        assert idle_received is True
        assert run_voice_web.audio_in_use.locked() is False
        assert run_voice_web.active_session_stop is None


def test_barge_in_interruption_during_playback(web_e2e_env):
    client = web_e2e_env.client

    async def slow_synthesize(text: str) -> bytes:
        await asyncio.sleep(0.04)
        return b"\x02\x00" * 1600

    web_e2e_env.speech.synthesize = slow_synthesize

    with client.websocket_connect(
        "/session", headers={"origin": "http://127.0.0.1", "host": "127.0.0.1"}
    ) as ws:
        ws.receive_json()
        ws.send_json({"action": "start", "mode": "barge_in"})

        while True:
            msg = ws.receive_json()
            if msg.get("kind") == "state" and msg.get("state") == "running":
                break

        ws.send_json({"action": "text", "text": "开始长文本播报"})

        # Wait for TTS to start synthesizing
        while True:
            raw = ws.receive()
            if "text" in raw:
                d = json.loads(raw["text"])
                if d.get("kind") == "tts_start":
                    break

        # Send speech frames to trigger barge-in interruption
        ws.send_bytes(b"\x01" * SPEECH_FRAME_BYTES)
        ws.send_bytes(b"\x01" * SPEECH_FRAME_BYTES)

        interrupted_received = False
        for _ in range(15):
            raw = ws.receive()
            if "text" in raw:
                d = json.loads(raw["text"])
                if d.get("kind") == "interrupted":
                    interrupted_received = True
                    break

        assert interrupted_received is True


def test_resume_conversation_restores_character_voice_and_context(web_e2e_env):
    client = web_e2e_env.client

    conv = history.create_conversation(
        character_id="example",
        character_name="千夏",
        voice="remielle",
        tts_engine="gpt_sovits",
    )
    conv_id = conv["id"]
    history.append_message(conv_id, "user", "我住在西湖区，喜欢吃小笼包")
    history.append_message(conv_id, "assistant", "你好！我已经记住了你住在西湖区并且爱吃小笼包。")
    history.update_conversation_summary(conv_id, "- 用户提到：住在西湖区，爱吃小笼包")

    with client.websocket_connect(
        "/session", headers={"origin": "http://127.0.0.1", "host": "127.0.0.1"}
    ) as ws:
        ws.receive_json()
        ws.send_json({"action": "start", "conversation_id": conv_id})

        char_info = None
        restored_conv_id = None
        for _ in range(10):
            raw = ws.receive()
            if "text" in raw:
                d = json.loads(raw["text"])
                if d.get("kind") == "character_info":
                    char_info = d
                elif d.get("kind") == "conversation":
                    restored_conv_id = d["id"]
                elif d.get("kind") == "state" and d.get("state") == "running":
                    break

        assert restored_conv_id == conv_id
        assert char_info is not None
        assert char_info["name"] == "千夏"
        assert char_info["voice"] == "remielle"
        assert char_info["tts_engine"] == "gpt_sovits"

        # Now continue conversation by asking a follow up question
        ws.send_json({"action": "text", "text": "我刚刚说我住在哪里？"})

        web_e2e_env.wait_for_event(ws, "output_complete")

        # Verify context was restored into LLM call history
        assert len(web_e2e_env.llm_client.chat.completions.call_history) >= 1
        last_call_messages = web_e2e_env.llm_client.chat.completions.call_history[-1]["messages"]
        message_contents = [m.get("content", "") for m in last_call_messages]

        assert any("住在西湖区" in content for content in message_contents)
        assert any("我刚刚说我住在哪里？" in content for content in message_contents)

        # Check database history was updated under the same conversation ID
        updated_conv = history.get_conversation(conv_id)
        assert updated_conv is not None
        assert len(updated_conv["messages"]) == 4
        assert updated_conv["messages"][2]["content"] == "我刚刚说我住在哪里？"
        assert updated_conv["messages"][3]["content"] == "你好！很高兴见到你。"


def test_tts_timeout_preserves_text_and_supports_retry_speak(web_e2e_env):
    client = web_e2e_env.client

    # Configure speech to fail on the first synthesize attempt with TimeoutError
    web_e2e_env.speech.synthesize_fail_exc = TimeoutError("TTS synthesis timed out")
    web_e2e_env.speech.fail_on_first_only = True

    with client.websocket_connect(
        "/session", headers={"origin": "http://127.0.0.1", "host": "127.0.0.1"}
    ) as ws:
        ws.receive_json()
        ws.send_json({"action": "text", "text": "测试TTS合成超时场景"})

        tts_error_event = None
        asst_msg_event = None

        for _ in range(25):
            raw = ws.receive()
            classified = web_e2e_env.classify_message(raw)
            if classified.get("kind") == "tts_error":
                tts_error_event = classified
            elif classified.get("kind") == "text" and classified.get("role") == "assistant":
                asst_msg_event = classified
            elif classified.get("kind") == "output_complete":
                break

        assert tts_error_event is not None
        assert "TTS synthesis timed out" in tts_error_event.get("error", "")

        # Assistant text response MUST still be delivered despite TTS failure
        assert asst_msg_event is not None
        assert asst_msg_event["text"] == "你好！很高兴见到你。"

        convs = history.list_conversations()
        assert len(convs) == 1
        conv_id = convs[0]["id"]
        conv_data = history.get_conversation(conv_id)
        assert len(conv_data["messages"]) == 2
        assert conv_data["messages"][1]["role"] == "assistant"
        assert conv_data["messages"][1]["content"] == "你好！很高兴见到你。"

        # Now perform retry_speak
        web_e2e_env.speech.synthesize_fail_exc = None
        ws.send_json({"action": "retry_speak"})

        received_audio = []
        output_complete_received = False
        for _ in range(20):
            raw = ws.receive()
            classified = web_e2e_env.classify_message(raw)
            if classified.get("kind") == "audio":
                received_audio.append(classified["bytes"])
            elif classified.get("kind") == "output_complete":
                output_complete_received = True
                break

        assert len(received_audio) >= 1
        assert output_complete_received is True

        # History must NOT duplicate the assistant message
        conv_data_after = history.get_conversation(conv_id)
        assert len(conv_data_after["messages"]) == 2


def test_client_abrupt_disconnect_cleans_up_resources(web_e2e_env):
    client = web_e2e_env.client

    with client.websocket_connect(
        "/session", headers={"origin": "http://127.0.0.1", "host": "127.0.0.1"}
    ) as ws:
        ws.receive_json()
        ws.send_json({"action": "start"})
        while True:
            msg = ws.receive_json()
            if msg.get("kind") == "state" and msg.get("state") == "running":
                break

        assert run_voice_web.audio_in_use.locked() is True
        assert run_voice_web.active_session_stop is not None

    # Exited websocket context - server finally block runs
    assert run_voice_web.audio_in_use.locked() is False
    assert run_voice_web.active_session_stop is None


def test_multitab_session_takeover(web_e2e_env):
    client = web_e2e_env.client

    with client.websocket_connect(
        "/session", headers={"host": "127.0.0.1", "origin": "http://127.0.0.1"}
    ) as ws1:
        ws1.receive_json()
        ws1.send_json({"action": "start"})
        while True:
            m = ws1.receive_json()
            if m.get("kind") == "state" and m.get("state") == "running":
                break

        # Tab 2 connects and starts session
        with client.websocket_connect(
            "/session", headers={"host": "127.0.0.1", "origin": "http://127.0.0.1"}
        ) as ws2:
            ws2.receive_json()
            ws2.send_json({"action": "start"})
            while True:
                m = ws2.receive_json()
                if m.get("kind") == "state" and m.get("state") == "running":
                    break

            # Tab 1 must receive idle
            tab1_idle = False
            for _ in range(5):
                m = ws1.receive_json()
                if m.get("kind") == "state" and m.get("state") == "idle":
                    tab1_idle = True
                    break
            assert tab1_idle is True

        # Once Tab 2 exits, audio_in_use should be completely released
        assert run_voice_web.audio_in_use.locked() is False


def test_multitab_occupied_lock_rejection(web_e2e_env):
    client = web_e2e_env.client

    # Simulate audio_in_use locked by an external entity with no active_session_stop
    run_voice_web.audio_in_use._lock.acquire()
    run_voice_web.active_session_stop = None

    try:
        with client.websocket_connect(
            "/session", headers={"host": "127.0.0.1", "origin": "http://127.0.0.1"}
        ) as ws:
            ws.receive_json()
            ws.send_json({"action": "start"})

            error_msg = None
            state_msg = None
            for _ in range(5):
                raw = ws.receive()
                if "text" in raw:
                    d = json.loads(raw["text"])
                    if d.get("kind") == "error":
                        error_msg = d
                    elif d.get("kind") == "state":
                        state_msg = d
                    if error_msg and state_msg:
                        break

            assert error_msg is not None
            assert "对话正在另一个页面使用" in error_msg.get("message", "")
            assert state_msg is not None
            assert state_msg["state"] == "idle"
    finally:
        run_voice_web.audio_in_use.release()
