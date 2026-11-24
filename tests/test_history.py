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

    assert history_migrations.migrate() == [1, 2]
    assert history_migrations.migrate() == []

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
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
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(messages)")}
        applied_versions = connection.execute("SELECT version FROM schema_migrations").fetchall()
    assert {"schema_migrations", "conversations", "messages"} <= tables
    assert conversation_columns == {
        "id": ("TEXT", 0, 1),
        "title": ("TEXT", 1, 0),
        "character_id": ("TEXT", 1, 0),
        "character_name": ("TEXT", 1, 0),
        "created_at": ("TEXT", 1, 0),
        "updated_at": ("TEXT", 1, 0),
        "voice": ("TEXT", 0, 0),
        "tts_engine": ("TEXT", 0, 0),
        "summary": ("TEXT", 0, 0),
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
    assert [row[0] for row in applied_versions] == [1, 2]


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


def test_migration_from_v1_to_v2_preserves_existing_conversations(monkeypatch, tmp_path):
    database = configure_database(monkeypatch, tmp_path)
    database.parent.mkdir(parents=True, exist_ok=True)

    # Apply only v1 migration initially
    v1_sql = (history_migrations.MIGRATIONS_DIR / "0001_conversation_history.sql").read_text(
        encoding="utf-8"
    )
    with sqlite3.connect(database) as conn:
        conn.executescript(v1_sql)
        conn.execute(
            """INSERT INTO conversations (id, title, character_id, character_name, created_at, updated_at)
               VALUES ('v1_conv', '旧会话', 'custom_char', '旧角色', '2026-11-20T10:00:00Z', '2026-11-20T10:00:00Z')"""
        )
        conn.execute(
            """INSERT INTO messages (conversation_id, role, content, created_at)
               VALUES ('v1_conv', 'user', '你好旧会话', '2026-11-20T10:00:01Z')"""
        )

    # Now run migrate() to upgrade to v2
    applied = history_migrations.migrate()
    assert applied == [2]

    # Existing v1 conversation is fully preserved with NULL for new columns
    conv = history.get_conversation("v1_conv")
    assert conv is not None
    assert conv["character_id"] == "custom_char"
    assert conv["character_name"] == "旧角色"
    assert conv["voice"] is None
    assert conv["tts_engine"] is None
    assert conv["summary"] is None
    assert len(conv["messages"]) == 1
    assert conv["messages"][0]["content"] == "你好旧会话"

    # Update settings and summary on the migrated conversation
    history.update_conversation_settings("v1_conv", voice="chinatsu", tts_engine="gpt_sovits")
    history.update_conversation_summary("v1_conv", "这是旧会话的前情摘要")

    updated = history.get_conversation("v1_conv")
    assert updated["voice"] == "chinatsu"
    assert updated["tts_engine"] == "gpt_sovits"
    assert updated["summary"] == "这是旧会话的前情摘要"


def test_create_and_update_conversation_settings_and_summary(monkeypatch, tmp_path):
    configure_database(monkeypatch, tmp_path)
    history_migrations.migrate()

    conv = history.create_conversation(
        "example", "千夏", voice="ling", tts_engine="gpt_sovits", summary="初始摘要"
    )
    assert conv["voice"] == "ling"
    assert conv["tts_engine"] == "gpt_sovits"
    assert conv["summary"] == "初始摘要"

    history.append_message(conv["id"], "user", "今天天气怎么样？")
    history.append_message(conv["id"], "assistant", "今天天气晴朗。")

    history.update_conversation_settings(conv["id"], voice="remielle", character_id="remielle_char")
    history.update_conversation_summary(conv["id"], "用户询问了天气")

    loaded = history.get_conversation(conv["id"])
    assert loaded["character_id"] == "remielle_char"
    assert loaded["voice"] == "remielle"
    assert loaded["tts_engine"] == "gpt_sovits"
    assert loaded["summary"] == "用户询问了天气"
    assert len(loaded["messages"]) == 2
