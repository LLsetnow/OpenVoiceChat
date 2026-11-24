import io
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import run_voice_web
from openvoicechat import history
from openvoicechat import migrate as history_migrations
from openvoicechat.conversation import Conversation
from run_voice_chat import Config


@pytest.fixture
def privacy_db_env(monkeypatch, tmp_path):
    data_dir = tmp_path / "privacy-test-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "conversations.sqlite3"

    monkeypatch.setattr(history, "DATA_DIR", data_dir)
    monkeypatch.setattr(history, "DATABASE", db_file)
    monkeypatch.setattr(history_migrations, "DATA_DIR", data_dir)
    monkeypatch.setattr(history_migrations, "DATABASE", db_file)
    monkeypatch.setattr(
        history_migrations,
        "MIGRATIONS_DIR",
        Path(__file__).resolve().parents[1] / "openvoicechat" / "migrations",
    )
    history_migrations.migrate()
    run_voice_web.app.state.database_status = None
    run_voice_web.app.state.migration_error = None

    # Mock voice_profile for Web sessions
    dummy_speech = MagicMock()
    dummy_speech.aclose = AsyncMock()
    dummy_config = MagicMock()
    monkeypatch.setattr(
        run_voice_web,
        "voice_profile",
        lambda **kwargs: (dummy_config, dummy_speech),
    )
    return data_dir, db_file


def test_delete_conversation_persists_and_cascades(privacy_db_env):
    _data_dir, db_file = privacy_db_env

    conv = history.create_conversation("mio", "千夏", voice="chinatsu", tts_engine="gpt_sovits")
    conv_id = conv["id"]
    history.append_message(conv_id, "user", "我的第一条秘密")
    history.append_message(conv_id, "assistant", "我会替你保守秘密")

    assert len(history.list_conversations()) == 1
    loaded = history.get_conversation(conv_id)
    assert loaded is not None
    assert len(loaded["messages"]) == 2

    # Verify in SQLite directly
    with sqlite3.connect(db_file) as conn:
        assert conn.execute("SELECT COUNT(*) FROM conversations WHERE id = ?", (conv_id,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (conv_id,)).fetchone()[0] == 2

    # Delete conversation
    deleted = history.delete_conversation(conv_id)
    assert deleted is True

    # Verify it is removed from history functions
    assert history.get_conversation(conv_id) is None
    assert len(history.list_conversations()) == 0

    # Verify SQLite cascade removal
    with sqlite3.connect(db_file) as conn:
        assert conn.execute("SELECT COUNT(*) FROM conversations WHERE id = ?", (conv_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (conv_id,)).fetchone()[0] == 0

    # Deleting non-existent conversation returns False
    assert history.delete_conversation("non_existent_id") is False


def test_delete_conversation_api_endpoint(privacy_db_env):
    conv = history.create_conversation("mio", "千夏")
    conv_id = conv["id"]
    history.append_message(conv_id, "user", "测试删除消息")
    history.append_message(conv_id, "assistant", "测试回复")

    client = TestClient(run_voice_web.app, base_url="http://127.0.0.1")

    # Delete via API
    resp = client.delete(f"/api/conversations/{conv_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["deleted"] is True
    assert data["id"] == conv_id

    # Verify 404 on subsequent get
    get_resp = client.get(f"/api/conversations/{conv_id}")
    assert get_resp.status_code == 404

    # Deleting non-existent returns 404
    del_missing = client.delete(f"/api/conversations/{conv_id}")
    assert del_missing.status_code == 404


def test_export_conversation_api_json_and_markdown(privacy_db_env):
    conv = history.create_conversation(
        "mio", "千夏", voice="chinatsu", tts_engine="gpt_sovits", summary="前情总结"
    )
    conv_id = conv["id"]
    history.append_message(conv_id, "user", "明天的会议是几点？")
    history.append_message(conv_id, "assistant", "明天上午十点整。")

    client = TestClient(run_voice_web.app, base_url="http://127.0.0.1")

    # Export JSON
    resp_json = client.get(f"/api/conversations/{conv_id}/export?format=json")
    assert resp_json.status_code == 200
    assert "application/json" in resp_json.headers["content-type"]
    assert "attachment;" in resp_json.headers["content-disposition"]
    json_data = resp_json.json()
    assert json_data["id"] == conv_id
    assert json_data["title"] == "明天的会议是几点？"
    assert json_data["character_name"] == "千夏"
    assert len(json_data["messages"]) == 2
    assert json_data["messages"][0]["role"] == "user"
    assert json_data["messages"][0]["content"] == "明天的会议是几点？"
    assert json_data["messages"][1]["role"] == "assistant"
    assert json_data["messages"][1]["content"] == "明天上午十点整。"

    # Export Markdown
    resp_md = client.get(f"/api/conversations/{conv_id}/export?format=markdown")
    assert resp_md.status_code == 200
    assert "text/markdown" in resp_md.headers["content-type"]
    assert "attachment;" in resp_md.headers["content-disposition"]
    md_text = resp_md.text
    assert "# 明天的会议是几点？" in md_text
    assert "角色" in md_text
    assert "千夏" in md_text
    assert "用户" in md_text
    assert "明天的会议是几点？" in md_text
    assert "明天上午十点整。" in md_text

    # Non-existent conversation export
    missing_resp = client.get("/api/conversations/not_here/export")
    assert missing_resp.status_code == 404


def test_ephemeral_mode_does_not_write_to_sqlite(privacy_db_env):
    _data_dir, db_file = privacy_db_env
    client = TestClient(run_voice_web.app, base_url="http://127.0.0.1")

    with client.websocket_connect(
        "/session", headers={"host": "127.0.0.1", "origin": "http://127.0.0.1"}
    ) as ws:
        ws.send_json({"action": "start", "character": "mio", "ephemeral": True})

        received = []
        for _ in range(6):
            msg = ws.receive_json()
            received.append(msg)
            if msg.get("kind") == "character_info":
                break

        conv_event = next((m for m in received if m.get("kind") == "conversation"), None)
        assert conv_event is not None
        assert conv_event.get("ephemeral") is True
        assert conv_event.get("id") is None

    # Verify SQLite database has zero conversations and zero messages
    with sqlite3.connect(db_file) as conn:
        assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0

    assert history.list_conversations() == []


@pytest.mark.asyncio
async def test_compact_logging_default_hides_sensitive_text(monkeypatch):
    # Ensure OVC_LOG_FULL_TEXT is not set
    monkeypatch.delenv("OVC_LOG_FULL_TEXT", raising=False)

    speech = MagicMock()
    speech.transcribe = AsyncMock(return_value="这是极其敏感的商业机密内容")
    speech.synthesize = AsyncMock(return_value=b"\x00\x00" * 800)

    # Mock client and stream for completion
    mock_chunk = MagicMock()
    mock_chunk.choices = [MagicMock()]
    mock_chunk.choices[0].delta = MagicMock()
    mock_chunk.choices[0].delta.content = "这是非常机密的回复内容。"

    async def mock_stream():
        yield mock_chunk

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_stream())
    mock_client.close = AsyncMock()

    log_buffer = io.StringIO()
    monkeypatch.setattr("sys.stdout", log_buffer)

    conv = Conversation(
        speech,
        client=mock_client,
        config=Config(llm_api_key="mock"),
        verbose=True,
    )
    assert conv.log_full_text is False

    # Run a text turn
    await conv._turn(text="这是极其敏感的商业机密内容")
    log_output = log_buffer.getvalue()

    # Verify stage indicators and metrics exist
    assert "turn start | chars=" in log_output
    assert "LLM first token" in log_output
    assert "LLM done" in log_output
    assert "TTS" in log_output
    assert "turn finished" in log_output

    # Verify sensitive text is NOT in log
    assert "这是极其敏感的商业机密内容" not in log_output
    assert "这是非常机密的回复内容" not in log_output


@pytest.mark.asyncio
async def test_explicit_log_full_text_outputs_text(monkeypatch):
    monkeypatch.setenv("OVC_LOG_FULL_TEXT", "1")

    speech = MagicMock()
    speech.transcribe = AsyncMock(return_value="公开测试内容")
    speech.synthesize = AsyncMock(return_value=b"\x00\x00" * 800)

    mock_chunk = MagicMock()
    mock_chunk.choices = [MagicMock()]
    mock_chunk.choices[0].delta = MagicMock()
    mock_chunk.choices[0].delta.content = "公开测试回复。"

    async def mock_stream():
        yield mock_chunk

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_stream())
    mock_client.close = AsyncMock()

    log_buffer = io.StringIO()
    monkeypatch.setattr("sys.stdout", log_buffer)

    conv = Conversation(
        speech,
        client=mock_client,
        config=Config(llm_api_key="mock"),
        verbose=True,
        log_full_text=True,
    )
    assert conv.log_full_text is True

    await conv._turn(text="公开测试内容")
    log_output = log_buffer.getvalue()

    # Full text is explicitly logged
    assert "公开测试内容" in log_output
    assert "公开测试回复" in log_output
