"""SQLite manifest. It is the single source of truth and the hand-off queue for later stages.

files.status:  pending -> downloaded            (this service)
               pending -> failed                (max_attempts exhausted; `retry` CLI resets)
               skipped                          (e.g. unknown size)
Downstream stages (transcribe, summarize) should select `status='downloaded'` and keep their own
state in their own tables/columns; they must never edit the columns owned here.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    remote_path   TEXT PRIMARY KEY,
    local_name    TEXT NOT NULL,
    length_bytes  INTEGER NOT NULL,
    modified      TEXT,
    metadata      TEXT NOT NULL,          -- device JSON verbatim
    status        TEXT NOT NULL DEFAULT 'pending',
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    next_attempt  REAL NOT NULL DEFAULT 0,
    bytes_received INTEGER,
    sha256        TEXT,
    first_seen    REAL NOT NULL,
    downloaded_at REAL
);
CREATE INDEX IF NOT EXISTS files_status ON files(status);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


@dataclass
class Row:
    remote_path: str
    local_name: str
    length_bytes: int
    modified: str | None
    metadata: dict
    status: str
    attempts: int
    next_attempt: float


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None)  # autocommit; explicit txns below
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(SCHEMA)
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(files)")}
        if "bookmarks_ms" not in cols:  # migration: JSON list of bookmark positions (ms)
            self.db.execute("ALTER TABLE files ADD COLUMN bookmarks_ms TEXT")

    def close(self) -> None:
        self.db.close()

    # ---- discovery ----
    def observe(self, remote_path: str, local_name: str, length_bytes: int,
                modified: str | None, metadata: dict) -> str:
        """Record a file seen in GetFileList. Returns 'new' | 'changed' | 'known'.

        A file whose declared size changed after we fetched it (it was still being written)
        goes back to pending so it is downloaded again.
        """
        meta = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        cur = self.db.execute("SELECT length_bytes, status FROM files WHERE remote_path=?",
                              (remote_path,)).fetchone()
        if cur is None:
            self.db.execute(
                "INSERT INTO files(remote_path,local_name,length_bytes,modified,metadata,status,first_seen)"
                " VALUES(?,?,?,?,?,?,?)",
                (remote_path, local_name, length_bytes, modified, meta,
                 "pending" if length_bytes > 0 else "skipped", time.time()))
            return "new"
        if cur["length_bytes"] != length_bytes and length_bytes > 0:
            self.db.execute(
                "UPDATE files SET length_bytes=?, modified=?, metadata=?, status='pending',"
                " attempts=0, next_attempt=0, last_error=NULL WHERE remote_path=?",
                (length_bytes, modified, meta, remote_path))
            return "changed"
        return "known"

    def adopt(self, remote_path: str, bytes_received: int, sha256: str) -> None:
        """Mark a file as downloaded without fetching (already present on disk)."""
        self.db.execute(
            "UPDATE files SET status='downloaded', bytes_received=?, sha256=?, downloaded_at=?,"
            " last_error=NULL WHERE remote_path=?", (bytes_received, sha256, time.time(), remote_path))

    # ---- work queue ----
    def due(self, now: float | None = None) -> list[Row]:
        now = time.time() if now is None else now
        rows = self.db.execute(
            "SELECT * FROM files WHERE status='pending' AND next_attempt<=? ORDER BY modified, remote_path",
            (now,)).fetchall()
        return [self._row(r) for r in rows]

    def pending_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM files WHERE status='pending'").fetchone()[0]

    def mark_downloaded(self, remote_path: str, bytes_received: int, sha256: str) -> None:
        self.db.execute(
            "UPDATE files SET status='downloaded', bytes_received=?, sha256=?, downloaded_at=?,"
            " last_error=NULL WHERE remote_path=?", (bytes_received, sha256, time.time(), remote_path))

    def mark_failed(self, remote_path: str, error: str, max_attempts: int,
                    backoff_base: float, backoff_max: float) -> str:
        r = self.db.execute("SELECT attempts FROM files WHERE remote_path=?", (remote_path,)).fetchone()
        attempts = r["attempts"] + 1
        status = "failed" if attempts >= max_attempts else "pending"
        delay = min(backoff_base * 2 ** (attempts - 1), backoff_max)
        self.db.execute(
            "UPDATE files SET attempts=?, status=?, last_error=?, next_attempt=? WHERE remote_path=?",
            (attempts, status, error[:500], time.time() + delay, remote_path))
        return status

    def retry_failed(self) -> int:
        cur = self.db.execute(
            "UPDATE files SET status='pending', attempts=0, next_attempt=0 WHERE status='failed'")
        return cur.rowcount

    def summary(self) -> dict[str, int]:
        return {r["status"]: r["n"] for r in
                self.db.execute("SELECT status, COUNT(*) n FROM files GROUP BY status")}

    def all(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM files ORDER BY modified, remote_path").fetchall()

    # ---- kv ----
    def set_kv(self, k: str, v: str) -> None:
        self.db.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))

    def get_kv(self, k: str) -> str | None:
        r = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return r["v"] if r else None

    @staticmethod
    def _row(r: sqlite3.Row) -> Row:
        return Row(r["remote_path"], r["local_name"], r["length_bytes"], r["modified"],
                   json.loads(r["metadata"]), r["status"], r["attempts"], r["next_attempt"])

    def next_due_time(self) -> float | None:
        r = self.db.execute("SELECT MIN(next_attempt) m FROM files WHERE status='pending'").fetchone()
        return r["m"]

    def local_name_for(self, remote_path: str) -> str:
        """Basename, unless a different remote file already uses it (then prefix the folder)."""
        parts = [p for p in remote_path.split("/") if p]
        base = parts[-1]
        taken = self.db.execute("SELECT 1 FROM files WHERE local_name=? AND remote_path<>?",
                                (base, remote_path)).fetchone()
        if taken and len(parts) >= 2:
            return f"{parts[-2]}_{base}"
        return base

    def set_bookmarks(self, remote_path: str, bookmarks_ms: list[int]) -> None:
        self.db.execute("UPDATE files SET bookmarks_ms=? WHERE remote_path=?",
                        (json.dumps(bookmarks_ms), remote_path))
