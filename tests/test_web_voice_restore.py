from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import run_voice_web
from openvoicechat import history
from openvoicechat import migrate as history_migrations


@pytest.fixture
def web_test_env(monkeypatch, tmp_path):
    database_dir = tmp_path / "web-test-data"
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

    # Mock voice_profile so it doesn't require real cloud API keys
    dummy_speech = MagicMock()
    dummy_speech.aclose = AsyncMock()
    dummy_config = MagicMock()
    monkeypatch.setattr(
        run_voice_web,
        "voice_profile",
        lambda **kwargs: (dummy_config, dummy_speech),
    )

    client = TestClient(run_voice_web.app, base_url="http://127.0.0.1")
    return client


def test_resume_conversation_only_with_conversation_id(web_test_env):
    client = web_test_env
    conv = history.create_conversation(
        character_id="example",
        character_name="千夏",
        voice="remielle",
        tts_engine="gpt_sovits",
    )

    with client.websocket_connect(
        "/session", headers={"host": "127.0.0.1", "origin": "http://127.0.0.1"}
    ) as ws:
        ws.send_json({"action": "start", "conversation_id": conv["id"]})

        received = []
        for _ in range(5):
            msg = ws.receive_json()
            received.append(msg)
            if msg.get("kind") == "character_info":
                break

        char_info = next((m for m in received if m.get("kind") == "character_info"), None)
        assert char_info is not None
        assert char_info["name"] == "千夏"
        assert char_info["voice"] == "remielle"
        assert char_info["tts_engine"] == "gpt_sovits"

        conv_event = next((m for m in received if m.get("kind") == "conversation"), None)
        assert conv_event is not None
        assert conv_event["id"] == conv["id"]


def test_resume_conversation_with_deleted_character_falls_back(web_test_env):
    client = web_test_env
    conv = history.create_conversation(
        character_id="deleted_char_xyz",
        character_name="已删角色",
        voice="remielle",
        tts_engine="gpt_sovits",
    )

    with client.websocket_connect(
        "/session", headers={"host": "127.0.0.1", "origin": "http://127.0.0.1"}
    ) as ws:
        ws.send_json({"action": "start", "conversation_id": conv["id"]})

        received = []
        for _ in range(6):
            msg = ws.receive_json()
            received.append(msg)
            if msg.get("kind") == "character_info":
                break

        notice = next((m for m in received if m.get("kind") == "notice"), None)
        assert notice is not None
        assert notice["message_key"] == "char_deleted_fallback"

        char_info = next((m for m in received if m.get("kind") == "character_info"), None)
        assert char_info is not None
        assert char_info["name"] in ("千夏", "Mio")


def test_resume_legacy_conversation_with_missing_voice_settings(web_test_env):
    client = web_test_env
    conv = history.create_conversation(
        character_id="example",
        character_name="千夏",
        voice=None,
        tts_engine=None,
    )

    with client.websocket_connect(
        "/session", headers={"host": "127.0.0.1", "origin": "http://127.0.0.1"}
    ) as ws:
        ws.send_json({"action": "start", "conversation_id": conv["id"]})

        received = []
        for _ in range(6):
            msg = ws.receive_json()
            received.append(msg)
            if msg.get("kind") == "character_info":
                break

        notice = next((m for m in received if m.get("kind") == "notice"), None)
        assert notice is not None
        assert notice["message_key"] == "legacy_settings_fallback"

        char_info = next((m for m in received if m.get("kind") == "character_info"), None)
        assert char_info is not None
        assert char_info["voice"] == "chinatsu"
        assert char_info["tts_engine"] == "gpt_sovits"

        # Database should be updated with fallback settings
        updated = history.get_conversation(conv["id"])
        assert updated["voice"] == "chinatsu"
        assert updated["tts_engine"] == "gpt_sovits"


def test_resume_conversation_with_explicit_user_override(web_test_env):
    client = web_test_env
    conv = history.create_conversation(
        character_id="example",
        character_name="千夏",
        voice="chinatsu",
        tts_engine="gpt_sovits",
    )

    with client.websocket_connect(
        "/session", headers={"host": "127.0.0.1", "origin": "http://127.0.0.1"}
    ) as ws:
        ws.send_json(
            {
                "action": "start",
                "conversation_id": conv["id"],
                "voice": "ling",
                "tts_engine": "cosyvoice",
            }
        )

        received = []
        for _ in range(5):
            msg = ws.receive_json()
            received.append(msg)
            if msg.get("kind") == "character_info":
                break

        char_info = next((m for m in received if m.get("kind") == "character_info"), None)
        assert char_info is not None
        assert char_info["voice"] == "ling"
        assert char_info["tts_engine"] == "cosyvoice"

        updated = history.get_conversation(conv["id"])
        assert updated["voice"] == "ling"
        assert updated["tts_engine"] == "cosyvoice"
