"""SQLite cache for indexed file/directory summaries."""

import sqlite3
import time
from pathlib import Path


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS file_info (
    path TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    file_mtime REAL NOT NULL,
    indexed_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS dir_info (
    path TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    file_list_hash TEXT NOT NULL,
    file_count INTEGER NOT NULL,
    indexed_at REAL NOT NULL,
    max_mtime REAL NOT NULL DEFAULT 0
);
"""


class IndexCache:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA_SQL)
        # Migration: add max_mtime if missing (pre-v2 schema)
        try:
            self._conn.execute("ALTER TABLE dir_info ADD COLUMN max_mtime REAL NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # column already exists

    def get_file_info(self, path: str) -> dict | None:
        row = self._conn.execute(
            "SELECT summary, file_hash, file_mtime FROM file_info WHERE path = ?",
            (path,),
        ).fetchone()
        if row:
            return {"summary": row[0], "file_hash": row[1], "file_mtime": row[2]}
        return None

    def upsert_file_info(self, path: str, summary: str, file_hash: str, file_size: int, file_mtime: float) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO file_info (path, summary, file_hash, file_size, file_mtime, indexed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (path, summary, file_hash, file_size, file_mtime, time.time()),
        )
        self._conn.commit()

    def get_dir_info(self, path: str) -> dict | None:
        row = self._conn.execute(
            "SELECT summary, file_list_hash, max_mtime FROM dir_info WHERE path = ?",
            (path,),
        ).fetchone()
        if row:
            return {"summary": row[0], "file_list_hash": row[1], "max_mtime": row[2]}
        return None

    def upsert_dir_info(self, path: str, summary: str, file_list_hash: str, file_count: int, max_mtime: float = 0) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO dir_info (path, summary, file_list_hash, file_count, indexed_at, max_mtime) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (path, summary, file_list_hash, file_count, time.time(), max_mtime),
        )
        self._conn.commit()

    def close(self):
        self._conn.close()
