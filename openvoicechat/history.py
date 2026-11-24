"""Local SQLite storage for web conversation history."""

from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

DATA_DIR = Path(
    os.environ.get("OPENVOICECHAT_DATA_DIR", Path(__file__).resolve().parent.parent / "data")
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


def create_conversation(
    character_id: str,
    character_name: str,
    voice: str | None = None,
    tts_engine: str | None = None,
    summary: str | None = None,
) -> dict:
    conversation_id = uuid.uuid4().hex
    timestamp = _now()
    with closing(_connect()) as connection, connection:
        connection.execute(
            """INSERT INTO conversations
               (id, title, character_id, character_name, voice, tts_engine, summary, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                conversation_id,
                "新对话",
                character_id,
                character_name,
                voice,
                tts_engine,
                summary,
                timestamp,
                timestamp,
            ),
        )
    return {
        "id": conversation_id,
        "title": "新对话",
        "character_id": character_id,
        "character_name": character_name,
        "voice": voice,
        "tts_engine": tts_engine,
        "summary": summary,
        "created_at": timestamp,
        "updated_at": timestamp,
        "messages": [],
    }


def list_conversations() -> list[dict]:
    with closing(_connect()) as connection, connection:
        rows = connection.execute(
            """SELECT c.id, c.title, c.character_id, c.character_name,
                      c.voice, c.tts_engine, c.summary,
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
            """SELECT id, title, character_id, character_name,
                      voice, tts_engine, summary, created_at, updated_at
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


def update_conversation_settings(
    conversation_id: str,
    character_id: str | None = None,
    character_name: str | None = None,
    voice: str | None = None,
    tts_engine: str | None = None,
) -> None:
    timestamp = _now()
    fields = []
    values = []
    if character_id is not None:
        fields.append("character_id = ?")
        values.append(character_id)
    if character_name is not None:
        fields.append("character_name = ?")
        values.append(character_name)
    if voice is not None:
        fields.append("voice = ?")
        values.append(voice)
    if tts_engine is not None:
        fields.append("tts_engine = ?")
        values.append(tts_engine)
    if not fields:
        return
    fields.append("updated_at = ?")
    values.append(timestamp)
    values.append(conversation_id)
    with closing(_connect()) as connection, connection:
        connection.execute(
            f"UPDATE conversations SET {', '.join(fields)} WHERE id = ?",
            values,
        )


def update_conversation_summary(conversation_id: str, summary: str) -> None:
    timestamp = _now()
    with closing(_connect()) as connection, connection:
        connection.execute(
            "UPDATE conversations SET summary = ?, updated_at = ? WHERE id = ?",
            (summary, timestamp, conversation_id),
        )


def delete_conversation(conversation_id: str) -> bool:
    with closing(_connect()) as connection, connection:
        connection.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        cursor = connection.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        return cursor.rowcount > 0


def export_conversation_markdown(conversation: dict) -> str:
    title = conversation.get("title") or "新对话"
    char_name = conversation.get("character_name") or "千夏"
    char_id = conversation.get("character_id") or "mio"
    created_at = conversation.get("created_at") or ""
    updated_at = conversation.get("updated_at") or ""
    lines = [
        f"# {title}",
        "",
        f"- **角色**: {char_name} ({char_id})",
        f"- **创建时间**: {created_at}",
        f"- **更新时间**: {updated_at}",
    ]
    if conversation.get("voice"):
        lines.append(f"- **音色**: {conversation['voice']}")
    if conversation.get("tts_engine"):
        lines.append(f"- **语音引擎**: {conversation['tts_engine']}")
    if conversation.get("summary"):
        lines.extend(["", "### 摘要", "", conversation["summary"]])
    lines.extend(["", "---", ""])
    for msg in conversation.get("messages", []):
        role_label = "用户" if msg.get("role") == "user" else char_name
        timestamp = msg.get("created_at") or ""
        time_str = f" ({timestamp})" if timestamp else ""
        lines.append(f"### {role_label}{time_str}")
        lines.append("")
        lines.append(msg.get("content") or "")
        lines.append("")
    return "\n".join(lines)
