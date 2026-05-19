from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from models import Task, User


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.init()

    def init(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL,
                    gmail TEXT NOT NULL UNIQUE,
                    drive_folder_path TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    selected_format TEXT,
                    filename TEXT,
                    local_path TEXT,
                    drive_path TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    started_at TEXT,
                    finished_at TEXT,
                    error TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(telegram_id)
                )
                """
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id)")

    def mark_unfinished_failed_on_startup(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE tasks
                SET status='failed', finished_at=CURRENT_TIMESTAMP,
                    error='Bot restarted before task finished'
                WHERE status IN ('queued', 'running')
                """
            )

    def add_user(self, telegram_id: int, username: str, gmail: str, drive_folder_path: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO users(telegram_id, username, gmail, drive_folder_path)
                VALUES (?, ?, ?, ?)
                """,
                (telegram_id, username, gmail, drive_folder_path),
            )

    def remove_user(self, telegram_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM users WHERE telegram_id=?", (telegram_id,))

    def get_user(self, telegram_id: int) -> Optional[User]:
        row = self._one("SELECT * FROM users WHERE telegram_id=?", (telegram_id,))
        return self._user(row) if row else None

    def list_users(self) -> list[User]:
        rows = self._all("SELECT * FROM users ORDER BY created_at DESC")
        return [self._user(r) for r in rows]

    def create_task(
        self,
        user_id: int,
        type_: str,
        source: str,
        selected_format: Optional[str] = None,
        filename: Optional[str] = None,
        local_path: Optional[str] = None,
        drive_path: Optional[str] = None,
    ) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO tasks(user_id, type, source, status, selected_format, filename, local_path, drive_path)
                VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)
                """,
                (user_id, type_, source, selected_format, filename, local_path, drive_path),
            )
            return int(cur.lastrowid)

    def get_task(self, task_id: int) -> Optional[Task]:
        row = self._one("SELECT * FROM tasks WHERE id=?", (task_id,))
        return self._task(row) if row else None

    def update_task(self, task_id: int, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "status", "selected_format", "filename", "local_path", "drive_path",
            "started_at", "finished_at", "error",
        }
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"Unsupported task fields: {bad}")
        assignments = ", ".join(f"{k}=?" for k in fields)
        values = list(fields.values()) + [task_id]
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE tasks SET {assignments} WHERE id=?", values)

    def set_started(self, task_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tasks SET status='running', started_at=CURRENT_TIMESTAMP WHERE id=?",
                (task_id,),
            )

    def set_finished(self, task_id: int, status: str, error: Optional[str] = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tasks SET status=?, finished_at=CURRENT_TIMESTAMP, error=? WHERE id=?",
                (status, error, task_id),
            )

    def user_tasks(self, user_id: int, limit: int = 10) -> list[Task]:
        rows = self._all(
            "SELECT * FROM tasks WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
        return [self._task(r) for r in rows]

    def visible_tasks(self, limit: int = 20) -> list[Task]:
        rows = self._all(
            "SELECT * FROM tasks WHERE status IN ('queued','running') ORDER BY id ASC LIMIT ?",
            (limit,),
        )
        return [self._task(r) for r in rows]

    def running_task_for_user(self, user_id: int) -> Optional[Task]:
        row = self._one(
            "SELECT * FROM tasks WHERE user_id=? AND status IN ('queued','running') ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        return self._task(row) if row else None

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def _all(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    @staticmethod
    def _user(row: sqlite3.Row) -> User:
        return User(**dict(row))

    @staticmethod
    def _task(row: sqlite3.Row) -> Task:
        return Task(**dict(row))
