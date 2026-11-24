import sqlite3
from pathlib import Path

from openvoicechat import history
from openvoicechat import migrate as history_migrations


def configure_database(monkeypatch, tmp_path):
    database_dir = tmp_path / "conversation-data"
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
    return database


def test_migration_is_versioned_and_idempotent(monkeypatch, tmp_path):
    database = configure_database(monkeypatch, tmp_path)

    assert history_migrations.migrate() == [1]
    assert history_migrations.migrate() == []

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        conversation_columns = {
            row[1]: (row[2], row[3], row[5])
            for row in connection.execute("PRAGMA table_info(conversations)")
        }
        message_columns = {
            row[1]: (row[2], row[3], row[5])
            for row in connection.execute("PRAGMA table_info(messages)")
        }
        foreign_keys = connection.execute("PRAGMA foreign_key_list(messages)").fetchall()
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(messages)")
        }
        applied_versions = connection.execute(
            "SELECT version FROM schema_migrations"
        ).fetchall()
    assert {"schema_migrations", "conversations", "messages"} <= tables
    assert conversation_columns == {
        "id": ("TEXT", 0, 1),
        "title": ("TEXT", 1, 0),
        "character_id": ("TEXT", 1, 0),
        "character_name": ("TEXT", 1, 0),
        "created_at": ("TEXT", 1, 0),
        "updated_at": ("TEXT", 1, 0),
    }
    assert message_columns == {
        "id": ("INTEGER", 0, 1),
        "conversation_id": ("TEXT", 1, 0),
        "role": ("TEXT", 1, 0),
        "content": ("TEXT", 1, 0),
        "created_at": ("TEXT", 1, 0),
    }
    assert len(foreign_keys) == 1
    assert "messages_by_conversation" in indexes
    assert [row[0] for row in applied_versions] == [1]


def test_conversation_history_can_be_listed_and_resumed(monkeypatch, tmp_path):
    configure_database(monkeypatch, tmp_path)
    history_migrations.migrate()
    conversation = history.create_conversation("mio", "千夏")

    history.append_message(conversation["id"], "user", "你好")
    history.append_message(conversation["id"], "assistant", "你好，很高兴见到你")

    listed = history.list_conversations()
    assert len(listed) == 1
    assert listed[0]["title"] == "你好"
    assert listed[0]["preview"] == "你好，很高兴见到你"

    resumed = history.get_conversation(conversation["id"])
    assert resumed is not None
    assert [(item["role"], item["content"]) for item in resumed["messages"]] == [
        ("user", "你好"),
        ("assistant", "你好，很高兴见到你"),
    ]
    assert history.get_conversation("missing") is None
