import hashlib
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path


class TaskStore:
    """Small SQLite-backed task history used for recovery and diagnostics."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    @staticmethod
    def task_id(url: str, quality: str) -> str:
        return hashlib.sha256(f"{url}\0{quality}".encode()).hexdigest()[:24]

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _database(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self):
        with self._lock, self._database() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS download_tasks (
                    id TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    quality TEXT NOT NULL,
                    status TEXT NOT NULL,
                    output_path TEXT,
                    error_message TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )

    def upsert(self, url: str, quality: str, status: str, **fields) -> str:
        task_id = self.task_id(url, quality)
        now = int(time.time())
        output_path = fields.get("output_path")
        error_message = fields.get("error_message")
        with self._lock, self._database() as db:
            db.execute(
                """
                INSERT INTO download_tasks
                    (id, url, quality, status, output_path, error_message,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    output_path=COALESCE(excluded.output_path, output_path),
                    error_message=excluded.error_message,
                    updated_at=excluded.updated_at
                """,
                (task_id, url, quality, status, output_path,
                 error_message, now, now),
            )
        return task_id

    def get(self, task_id: str):
        with self._lock, self._database() as db:
            row = db.execute(
                "SELECT * FROM download_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return dict(row) if row else None

    def pending(self):
        with self._lock, self._database() as db:
            rows = db.execute(
                """
                SELECT * FROM download_tasks
                WHERE status IN ('PENDING', 'RESOLVING', 'DOWNLOADING', 'MERGING')
                ORDER BY created_at
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def recent(self, limit: int = 100):
        limit = max(1, min(int(limit), 500))
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT * FROM download_tasks "
                "ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]
