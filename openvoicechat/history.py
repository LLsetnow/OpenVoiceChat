"""Local SQLite storage for web conversation history."""

from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

DATA_DIR = Path(
    os.environ.get(
        "OPENVOICECHAT_DATA_DIR", Path(__file__).resolve().parent.parent / "data"
    )
)
DATABASE = DATA_DIR / "conversations.sqlite3"


def _connect() -> sqlite3.Connection:
    if not DATABASE.is_file():
        raise RuntimeError(
            "Conversation database is not initialized. Run `python -m openvoicechat.migrate`."
        )
    connection = sqlite3.connect(DATABASE, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def create_conversation(character_id: str, character_name: str) -> dict:
    conversation_id = uuid.uuid4().hex
    timestamp = _now()
    with closing(_connect()) as connection, connection:
        connection.execute(
            """INSERT INTO conversations
               (id, title, character_id, character_name, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (conversation_id, "新对话", character_id, character_name, timestamp, timestamp),
        )
    return {
        "id": conversation_id,
        "title": "新对话",
        "character_id": character_id,
        "character_name": character_name,
        "created_at": timestamp,
        "updated_at": timestamp,
        "messages": [],
    }


def list_conversations() -> list[dict]:
    with closing(_connect()) as connection, connection:
        rows = connection.execute(
            """SELECT c.id, c.title, c.character_id, c.character_name,
                      c.created_at, c.updated_at, COUNT(m.id) AS message_count,
                      (SELECT content FROM messages WHERE conversation_id = c.id
                       ORDER BY id DESC LIMIT 1) AS preview
               FROM conversations AS c
               LEFT JOIN messages AS m ON m.conversation_id = c.id
               GROUP BY c.id
               HAVING COUNT(m.id) > 0
               ORDER BY c.updated_at DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


def get_conversation(conversation_id: str) -> dict | None:
    with closing(_connect()) as connection, connection:
        row = connection.execute(
            """SELECT id, title, character_id, character_name, created_at, updated_at
               FROM conversations WHERE id = ?""",
            (conversation_id,),
        ).fetchone()
        if row is None:
            return None
        conversation = dict(row)
        conversation["messages"] = [
            dict(message)
            for message in connection.execute(
                """SELECT role, content, created_at FROM messages
                   WHERE conversation_id = ? ORDER BY id""",
                (conversation_id,),
            ).fetchall()
        ]
    return conversation


def append_message(conversation_id: str, role: str, content: str) -> None:
    if role not in {"user", "assistant"} or not content.strip():
        return
    timestamp = _now()
    with closing(_connect()) as connection, connection:
        conversation = connection.execute(
            "SELECT title FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if conversation is None:
            return
        title = conversation["title"]
        if role == "user" and title == "新对话":
            title = content.strip().replace("\n", " ")[:48]
        connection.execute(
            """INSERT INTO messages (conversation_id, role, content, created_at)
               VALUES (?, ?, ?, ?)""",
            (conversation_id, role, content, timestamp),
        )
        connection.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, timestamp, conversation_id),
        )
