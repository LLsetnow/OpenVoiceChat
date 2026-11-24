import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from openvoicechat import history
from openvoicechat import migrate as history_migrations
from openvoicechat.migrate import DatabaseMigrationError
from run_voice_web import app


@pytest.fixture
def clean_db_env(monkeypatch, tmp_path):
    data_dir = tmp_path / "fresh_data"
    db_file = data_dir / "conversations.sqlite3"
    migrations_dir = Path(__file__).resolve().parents[1] / "openvoicechat" / "migrations"
    monkeypatch.setattr(history, "DATA_DIR", data_dir)
    monkeypatch.setattr(history, "DATABASE", db_file)
    monkeypatch.setattr(history_migrations, "DATA_DIR", data_dir)
    monkeypatch.setattr(history_migrations, "DATABASE", db_file)
    monkeypatch.setattr(history_migrations, "MIGRATIONS_DIR", migrations_dir)
    app.state.database_status = None
    app.state.migration_error = None
    return data_dir, db_file


def test_empty_data_dir_first_startup_auto_migrates(clean_db_env):
    _data_dir, db_file = clean_db_env
    assert not db_file.exists()

    with TestClient(app) as client:
        status_resp = client.get("/api/status")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        assert status_data["status"] == "ready"
        assert status_data["database"]["applied"] == [1]
        assert db_file.is_file()

        resp = client.get("/api/conversations")
        assert resp.status_code == 200
        assert resp.json() == {"conversations": []}

        conv = history.create_conversation("mio", "千夏")
        history.append_message(conv["id"], "user", "你好，千夏")
        history.append_message(conv["id"], "assistant", "你好！很高兴见到你。")

        resp2 = client.get("/api/conversations")
        assert resp2.status_code == 200
        conversations = resp2.json()["conversations"]
        assert len(conversations) == 1
        assert conversations[0]["id"] == conv["id"]
        assert conversations[0]["title"] == "你好，千夏"


def test_repeated_startup_with_existing_database_is_idempotent(clean_db_env):
    _data_dir, _db_file = clean_db_env

    with TestClient(app) as client:
        conv = history.create_conversation("mio", "千夏")
        history.append_message(conv["id"], "user", "已有历史记录")
        history.append_message(conv["id"], "assistant", "回复已有历史")

    app.state.database_status = None
    app.state.migration_error = None

    with TestClient(app) as client:
        status_resp = client.get("/api/status")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        assert status_data["status"] == "ready"
        assert status_data["database"]["applied"] == []

        resp = client.get("/api/conversations")
        assert resp.status_code == 200
        conversations = resp.json()["conversations"]
        assert len(conversations) == 1
        assert conversations[0]["id"] == conv["id"]
        assert conversations[0]["preview"] == "回复已有历史"


def test_migration_failure_simulation_api_and_websocket_recovery(clean_db_env, monkeypatch):
    _data_dir, _db_file = clean_db_env

    def failing_migrate():
        raise DatabaseMigrationError("Simulated disk I/O migration failure")

    monkeypatch.setattr("run_voice_web.migrate", failing_migrate)

    with TestClient(app) as client:
        status_resp = client.get("/api/status")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        assert status_data["status"] == "degraded"
        assert status_data["database"]["status"] == "error"
        assert "python -m openvoicechat.migrate" in status_data["database"]["recovery"]
        assert "Simulated disk I/O migration failure" in status_data["database"]["error"]

        conv_resp = client.get("/api/conversations")
        assert conv_resp.status_code == 503
        detail = conv_resp.json()["detail"]
        assert detail["error"] == "database_migration_failed"
        assert detail["message_key"] == "db_migration_failed"
        assert "python -m openvoicechat.migrate" in detail["recovery"]
        assert "python -m openvoicechat.migrate" in detail["message"]
        assert "python -m openvoicechat.migrate" in detail["message_en"]

        with client.websocket_connect("/session", headers={"origin": "http://127.0.0.1:18768"}) as ws:
            events = []
            for _ in range(2):
                msg = ws.receive_json()
                events.append(msg)
            error_event = next((e for e in events if e.get("kind") == "error"), None)
            assert error_event is not None
            assert error_event["message_key"] == "db_migration_failed"
            assert "python -m openvoicechat.migrate" in error_event["recovery"]

            ws.send_json({"action": "start", "character": "mio"})
            resp_error = ws.receive_json()
            assert resp_error["kind"] == "error"
            assert resp_error["code"] == "DATABASE_MIGRATION_FAILED"
            assert resp_error["message_key"] == "db_migration_failed"
            assert "python -m openvoicechat.migrate" in resp_error["recovery"]

            resp_state = ws.receive_json()
            assert resp_state["kind"] == "state"
            assert resp_state["state"] == "idle"

            ws.send_json({"action": "init"})
            resp_chars = ws.receive_json()
            assert resp_chars["kind"] == "characters"


def test_i18n_keys_consistency():
    i18n_path = Path(__file__).resolve().parents[1] / "web" / "i18n.js"
    content = i18n_path.read_text(encoding="utf-8")

    zh_match = re.search(r'"zh-CN":\s*\{(.*?)\n\s*\},', content, re.DOTALL)
    en_match = re.search(r'(?:"en"|en):\s*\{(.*?)\n\s*\},?\s*\}', content, re.DOTALL)
    assert zh_match and en_match

    def extract_keys(block):
        return set(re.findall(r'^\s*([a-zA-Z0-9_]+):', block, re.MULTILINE))

    zh_keys = extract_keys(zh_match.group(1))
    en_keys = extract_keys(en_match.group(1))

    assert "db_migration_failed" in zh_keys
    assert "db_migration_failed" in en_keys
    assert "db_migration_recovery" in zh_keys
    assert "db_migration_recovery" in en_keys
    assert zh_keys == en_keys
