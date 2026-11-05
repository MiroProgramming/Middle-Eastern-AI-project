"""Cuppy's long-term storage: a single SQLite file.

Holds memories (facts about you), reminders, conversation history and a small
key/value table for bookkeeping.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    due_at TEXT NOT NULL,              -- ISO 8601, UTC
    repeat TEXT NOT NULL DEFAULT 'none',
    channel TEXT NOT NULL DEFAULT 'default',
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | done | cancelled | failed
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    messages TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,                  -- ISO 8601, UTC
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL,
    cache_write_tokens INTEGER NOT NULL,
    web_searches INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Reminder:
    id: int
    text: str
    due_at: datetime
    repeat: str
    channel: str
    status: str
    attempts: int


class Database:
    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # --- memories --------------------------------------------------------
    def add_memory(self, content: str, category: str = "general") -> int:
        cur = self.conn.execute(
            "INSERT INTO memories (content, category, created_at) VALUES (?, ?, ?)",
            (content, category, utcnow().isoformat()),
        )
        self.conn.commit()
        return cur.lastrowid

    def search_memories(self, query: str, limit: int = 20) -> list[sqlite3.Row]:
        words = [w for w in query.lower().split() if len(w) > 2] or [query.lower()]
        clause = " OR ".join("lower(content) LIKE ?" for _ in words)
        params = [f"%{w}%" for w in words]
        return self.conn.execute(
            f"SELECT * FROM memories WHERE {clause} ORDER BY id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()

    def list_memories(self, category: str | None = None, limit: int = 200) -> list[sqlite3.Row]:
        if category:
            return self.conn.execute(
                "SELECT * FROM memories WHERE category = ? ORDER BY id LIMIT ?", (category, limit)
            ).fetchall()
        return self.conn.execute("SELECT * FROM memories ORDER BY id LIMIT ?", (limit,)).fetchall()

    def delete_memory(self, memory_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # --- reminders -------------------------------------------------------
    def add_reminder(self, text: str, due_at: datetime, repeat: str = "none", channel: str = "default") -> int:
        cur = self.conn.execute(
            "INSERT INTO reminders (text, due_at, repeat, channel, created_at) VALUES (?, ?, ?, ?, ?)",
            (text, due_at.astimezone(timezone.utc).isoformat(), repeat, channel, utcnow().isoformat()),
        )
        self.conn.commit()
        return cur.lastrowid

    def _row_to_reminder(self, row: sqlite3.Row) -> Reminder:
        return Reminder(
            id=row["id"],
            text=row["text"],
            due_at=datetime.fromisoformat(row["due_at"]),
            repeat=row["repeat"],
            channel=row["channel"],
            status=row["status"],
            attempts=row["attempts"],
        )

    def pending_reminders(self) -> list[Reminder]:
        rows = self.conn.execute(
            "SELECT * FROM reminders WHERE status = 'pending' ORDER BY due_at"
        ).fetchall()
        return [self._row_to_reminder(r) for r in rows]

    def due_reminders(self, now: datetime | None = None) -> list[Reminder]:
        now = now or utcnow()
        return [r for r in self.pending_reminders() if r.due_at <= now]

    def get_reminder(self, reminder_id: int) -> Reminder | None:
        row = self.conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        return self._row_to_reminder(row) if row else None

    def set_reminder_status(self, reminder_id: int, status: str) -> bool:
        cur = self.conn.execute("UPDATE reminders SET status = ? WHERE id = ?", (status, reminder_id))
        self.conn.commit()
        return cur.rowcount > 0

    def reschedule_reminder(self, reminder_id: int, due_at: datetime) -> None:
        self.conn.execute(
            "UPDATE reminders SET due_at = ?, attempts = 0 WHERE id = ?",
            (due_at.astimezone(timezone.utc).isoformat(), reminder_id),
        )
        self.conn.commit()

    def bump_attempts(self, reminder_id: int) -> int:
        self.conn.execute("UPDATE reminders SET attempts = attempts + 1 WHERE id = ?", (reminder_id,))
        self.conn.commit()
        row = self.conn.execute("SELECT attempts FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        return row["attempts"]

    # --- conversations ---------------------------------------------------
    def load_conversation(self, conversation_id: str) -> list[dict]:
        row = self.conn.execute(
            "SELECT messages FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        return json.loads(row["messages"]) if row else []

    def save_conversation(self, conversation_id: str, messages: list[dict]) -> None:
        self.conn.execute(
            "INSERT INTO conversations (id, messages, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET messages = excluded.messages, updated_at = excluded.updated_at",
            (conversation_id, json.dumps(messages), utcnow().isoformat()),
        )
        self.conn.commit()

    # --- usage / cost tracking -------------------------------------------
    def record_usage(
        self, model: str, input_tokens: int, output_tokens: int, cache_read: int, cache_write: int,
        web_searches: int, cost_usd: float,
    ) -> None:
        self.conn.execute(
            "INSERT INTO usage (at, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,"
            " web_searches, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (utcnow().isoformat(), model, input_tokens, output_tokens, cache_read, cache_write, web_searches, cost_usd),
        )
        self.conn.commit()

    def usage_since(self, since: datetime) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT model, COUNT(*) AS requests, SUM(input_tokens) AS input_tokens,"
            " SUM(output_tokens) AS output_tokens, SUM(cache_read_tokens) AS cache_read_tokens,"
            " SUM(cache_write_tokens) AS cache_write_tokens, SUM(web_searches) AS web_searches,"
            " SUM(cost_usd) AS cost_usd FROM usage WHERE at >= ? GROUP BY model ORDER BY cost_usd DESC",
            (since.astimezone(timezone.utc).isoformat(),),
        ).fetchall()

    def cost_since(self, since: datetime) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS c FROM usage WHERE at >= ?",
            (since.astimezone(timezone.utc).isoformat(),),
        ).fetchone()
        return float(row["c"])

    # --- key/value -------------------------------------------------------
    def get(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()
