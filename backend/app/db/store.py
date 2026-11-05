"""SQLite in WAL mode: tasks and their event streams."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, ticket TEXT NOT NULL, status TEXT NOT NULL,
  outcome TEXT NOT NULL, outcome_rule TEXT, issue_url TEXT, explanation TEXT,
  client_ip TEXT, tokens_used INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  task_id TEXT NOT NULL, seq INTEGER NOT NULL, at TEXT NOT NULL, type TEXT NOT NULL,
  text TEXT, data TEXT, PRIMARY KEY (task_id, seq)
);
"""
DONE = "done"
UPDATABLE = {"status", "outcome", "outcome_rule", "issue_url", "explanation"}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


class Store:
    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def create_task(self, task_id: str, ticket: str, client_ip: str, kind: str = "ticket") -> None:
        with self.lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO tasks (id, kind, ticket, status, outcome, client_ip, created_at) VALUES (?,?,?,?,?,?,?)",
                (task_id, kind, ticket, "queued", "PENDING", client_ip, now_iso()),
            )

    def update_task(self, task_id: str, **fields: object) -> None:
        unknown = set(fields) - UPDATABLE
        if unknown:
            raise ValueError(f"Not updatable: {sorted(unknown)}")
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self.lock, self._connect() as connection:
            connection.execute(f"UPDATE tasks SET {assignments} WHERE id = ?", (*fields.values(), task_id))

    def add_tokens(self, task_id: str, tokens: int) -> None:
        with self.lock, self._connect() as connection:
            connection.execute("UPDATE tasks SET tokens_used = tokens_used + ? WHERE id = ?", (tokens, task_id))

    def get_task(self, task_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    def tokens_used_since(self, since_iso: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(tokens_used), 0) AS total FROM tasks WHERE created_at >= ?", (since_iso,)
            ).fetchone()
        return int(row["total"])

    def count_recent_from(self, client_ip: str, since_iso: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM tasks WHERE client_ip = ? AND created_at >= ?", (client_ip, since_iso)
            ).fetchone()
        return int(row["total"])

    def unfinished_tasks(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute("SELECT id FROM tasks WHERE status != ?", (DONE,)).fetchall()
        return [row["id"] for row in rows]

    def add_event(self, task_id: str, event_type: str, text: str | None = None, data: dict | None = None) -> None:
        with self.lock, self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(seq), -1) + 1 AS next FROM events WHERE task_id = ?", (task_id,)
            ).fetchone()
            connection.execute(
                "INSERT INTO events (task_id, seq, at, type, text, data) VALUES (?,?,?,?,?,?)",
                (task_id, row["next"], now_iso(), event_type, (text or "")[:4000] or None, json.dumps(data) if data else None),
            )

    def events_after(self, task_id: str, seq: int) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE task_id = ? AND seq > ? ORDER BY seq", (task_id, seq)
            ).fetchall()
        events = []
        for row in rows:
            event = {"task_id": row["task_id"], "seq": row["seq"], "at": row["at"], "type": row["type"]}
            if row["text"]:
                event["text"] = row["text"]
            if row["data"]:
                event["data"] = json.loads(row["data"])
            events.append(event)
        return events
