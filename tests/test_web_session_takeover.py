import json
import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import run_voice_web
from openvoicechat import history
from openvoicechat import migrate as history_migrations
from openvoicechat.config import Config


class FakeSpeech:
    def __init__(self):
        self.aclose = AsyncMock()

    async def transcribe(self, pcm16: bytes) -> str:
        return "你好"

    async def synthesize(self, text: str) -> bytes:
        return b"\x02\x00" * 800


class FakeCompletions:
    async def create(self, **kwargs):
        async def stream():
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="你好，我是千夏。"))]
            )

        return stream()


class FakeLLMClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=FakeCompletions())

    async def close(self):
        pass


@pytest.fixture
def web_session_env(monkeypatch, tmp_path):
    database_dir = tmp_path / "web-session-data"
    database_dir.mkdir(parents=True, exist_ok=True)
    database = database_dir / "conversations.sqlite3"

    monkeypatch.setattr(history, "DATA_DIR", database_dir)
    monkeypatch.setattr(history, "DATABASE", database)
    monkeypatch.setattr(history_migrations, "DATA_DIR", database_dir)
    monkeypatch.setattr(history_migrations, "DATABASE", database)
    monkeypatch.setattr(
        history_migrations,
        "MIGRATIONS_DIR",
        Path(__file__).resolve().parents[1] / "openvoicechat" / "migrations",
    )
    history_migrations.migrate()

    run_voice_web.active_session_id = None
    run_voice_web.active_session_stop = None
    run_voice_web.connected_sessions.clear()

    fake_speech = FakeSpeech()
    fake_config = Config(
        endpoint_silence_ms=64,
        min_speech_ms=32,
        pre_roll_ms=0,
        output_chunk_ms=100,
    )

    monkeypatch.setattr(
        run_voice_web,
        "voice_profile",
        lambda **kwargs: (fake_config, fake_speech),
    )

    orig_conv_init = run_voice_web.Conversation.__init__

    def patched_conv_init(self, *args, **kwargs):
        kwargs["client"] = FakeLLMClient()
        orig_conv_init(self, *args, **kwargs)

    monkeypatch.setattr(run_voice_web.Conversation, "__init__", patched_conv_init)

    client = TestClient(run_voice_web.app, base_url="http://127.0.0.1")
    return client


def test_multi_tab_occupied_and_explicit_takeover(web_session_env):
    client = web_session_env

    with client.websocket_connect(
        "/session", headers={"host": "127.0.0.1", "origin": "http://127.0.0.1"}
    ) as ws1:
        ws1.send_json({"action": "start"})

        for _ in range(10):
            msg = ws1.receive_json()
            if msg.get("kind") == "state" and msg.get("state") == "running":
                break

        assert run_voice_web.active_session_id is not None

        with client.websocket_connect(
            "/session", headers={"host": "127.0.0.1", "origin": "http://127.0.0.1"}
        ) as ws2:
            ws2.send_json({"action": "init"})
            status_event = None
            for _ in range(5):
                m = ws2.receive_json()
                if m.get("kind") == "session_status":
                    status_event = m
                    break
            assert status_event is not None
            assert status_event["occupied"] is True

            ws2.send_json({"action": "start"})
            occupied_msg = None
            for _ in range(5):
                m = ws2.receive_json()
                if m.get("kind") == "occupied":
                    occupied_msg = m
                    break
            assert occupied_msg is not None
            assert occupied_msg["code"] == "SESSION_OCCUPIED"
            assert occupied_msg["message_key"] == "session_occupied"

            assert run_voice_web.active_session_id != status_event["session_id"]

            ws2.send_json({"action": "takeover"})

            tab1_taken_over = None
            for _ in range(5):
                msg = ws1.receive_json()
                if msg.get("kind") == "taken_over":
                    tab1_taken_over = msg
                    break
            assert tab1_taken_over is not None
            assert tab1_taken_over["code"] == "SESSION_TAKEN_OVER"
            assert tab1_taken_over["message_key"] == "session_taken_over"

            tab2_running = False
            for _ in range(10):
                msg = ws2.receive_json()
                if msg.get("kind") == "state" and msg.get("state") == "running":
                    tab2_running = True
                    break
            assert tab2_running is True
            assert run_voice_web.active_session_id == status_event["session_id"]


def test_turn_id_consistency_in_audio_and_subtitles(web_session_env):
    client = web_session_env

    with client.websocket_connect(
        "/session", headers={"host": "127.0.0.1", "origin": "http://127.0.0.1"}
    ) as ws:
        ws.send_json({"action": "start"})

        for _ in range(10):
            msg = ws.receive_json()
            if msg.get("kind") == "state" and msg.get("state") == "running":
                break

        ws.send_json({"action": "text", "text": "你好"})

        received_turns = set()
        audio_received = False
        output_complete = False

        for _ in range(20):
            raw = ws.receive()
            if raw.get("bytes"):
                audio_bytes = raw["bytes"]
                assert len(audio_bytes) >= 4
                (chunk_turn_id,) = struct.unpack(">I", audio_bytes[:4])
                assert chunk_turn_id > 0
                received_turns.add(chunk_turn_id)
                audio_received = True
            elif raw.get("text"):
                data = json.loads(raw["text"])
                if data.get("turn_id"):
                    received_turns.add(data["turn_id"])
                if data.get("kind") == "output_complete":
                    output_complete = True
                    break

        assert audio_received is True
        assert output_complete is True
        assert len(received_turns) == 1
        turn_id = next(iter(received_turns))

        ws.send_json({"action": "playback_started", "turn_id": turn_id})
        ws.send_json({"action": "playback_ended", "turn_id": turn_id})
        ws.send_json({"action": "playback_ended", "turn_id": 9999})

        ws.send_json({"action": "stop", "turn_id": turn_id})
        stop_msg = None
        for _ in range(5):
            m = ws.receive_json()
            if m.get("kind") == "state" and m.get("state") == "idle":
                stop_msg = m
                break
        assert stop_msg is not None
        assert run_voice_web.active_session_id is None


def test_session_cleanup_on_disconnect(web_session_env):
    client = web_session_env

    with client.websocket_connect(
        "/session", headers={"host": "127.0.0.1", "origin": "http://127.0.0.1"}
    ) as ws:
        ws.send_json({"action": "start"})
        for _ in range(10):
            msg = ws.receive_json()
            if msg.get("kind") == "state" and msg.get("state") == "running":
                break
        assert run_voice_web.active_session_id is not None

    assert run_voice_web.active_session_id is None
    assert not run_voice_web.audio_in_use.locked()


def test_i18n_translation_keys_completeness():
    i18n_path = Path(__file__).resolve().parents[1] / "web" / "i18n.js"
    content = i18n_path.read_text(encoding="utf-8")

    for key in ["session_occupied", "takeover_session", "session_taken_over"]:
        assert f"{key}:" in content
