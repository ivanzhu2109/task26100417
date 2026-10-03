"""SQLite-backed persistence for upload state.

Stores upload metadata, confirmed byte intervals and the publish journal.
All mutating operations are committed in single SQLite transactions so a
killed process never leaves half-acknowledged state behind.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .errors import UploadExistsError, UploadNotFoundError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS uploads (
    upload_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    length      INTEGER NOT NULL,
    staging     TEXT NOT NULL,
    published   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS intervals (
    upload_id   TEXT NOT NULL,
    start       INTEGER NOT NULL,
    end         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intervals_upload ON intervals(upload_id, start);
CREATE TABLE IF NOT EXISTS publish_journal (
    upload_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    staging     TEXT NOT NULL
);
"""


class Repository:
    """Owns the SQLite connection and all state transitions."""

    def __init__(self, db_path: Path) -> None:
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # -- upload lifecycle -------------------------------------------------

    def create_upload(self, upload_id: str, name: str, length: int,
                      staging: str) -> None:
        """Insert a new upload row, or return if an identical one exists.

        Raises UploadExistsError when the id exists with different
        parameters.
        """
        row = self._conn.execute(
            "SELECT name, length FROM uploads WHERE upload_id = ?",
            (upload_id,),
        ).fetchone()
        if row is not None:
            if row["name"] != name or row["length"] != length:
                raise UploadExistsError(
                    f"upload {upload_id!r} exists with different parameters"
                )
            return
        with self._conn:
            self._conn.execute(
                "INSERT INTO uploads (upload_id, name, length, staging)"
                " VALUES (?, ?, ?, ?)",
                (upload_id, name, length, staging),
            )

    def get_upload(self, upload_id: str) -> sqlite3.Row:
        """Return the upload row or raise UploadNotFoundError."""
        row = self._conn.execute(
            "SELECT * FROM uploads WHERE upload_id = ?", (upload_id,)
        ).fetchone()
        if row is None:
            raise UploadNotFoundError(f"unknown upload id {upload_id!r}")
        return row

    # -- confirmed intervals ----------------------------------------------

    def get_intervals(self, upload_id: str) -> list[tuple[int, int]]:
        """Return sorted disjoint confirmed intervals for an upload."""
        rows = self._conn.execute(
            "SELECT start, end FROM intervals WHERE upload_id = ?"
            " ORDER BY start",
            (upload_id,),
        ).fetchall()
        return [(r["start"], r["end"]) for r in rows]

    def add_coverage(self, upload_id: str, start: int, end: int) -> None:
        """Merge [start, end) into the confirmed intervals and commit."""
        rows = self._conn.execute(
            "SELECT start, end FROM intervals"
            " WHERE upload_id = ? AND start <= ? AND end >= ?"
            " ORDER BY start",
            (upload_id, end, start),
        ).fetchall()
        merged_start, merged_end = start, end
        for r in rows:
            merged_start = min(merged_start, r["start"])
            merged_end = max(merged_end, r["end"])
        with self._conn:
            self._conn.execute(
                "DELETE FROM intervals"
                " WHERE upload_id = ? AND start <= ? AND end >= ?",
                (upload_id, end, start),
            )
            self._conn.execute(
                "INSERT INTO intervals (upload_id, start, end) VALUES (?, ?, ?)",
                (upload_id, merged_start, merged_end),
            )

    # -- publish journal ----------------------------------------------------

    def journal_publish(self, upload_id: str, name: str,
                        staging: str) -> None:
        """Record the intent to publish (idempotent)."""
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO publish_journal (upload_id, name, staging)"
                " VALUES (?, ?, ?)",
                (upload_id, name, staging),
            )

    def pending_publishes(self) -> list[sqlite3.Row]:
        """Return journal rows for publishes interrupted before completion."""
        return self._conn.execute(
            "SELECT upload_id, name, staging FROM publish_journal"
        ).fetchall()

    def finish_publish(self, upload_id: str) -> None:
        """Mark the upload published and drop its journal row."""
        with self._conn:
            self._conn.execute(
                "UPDATE uploads SET published = 1 WHERE upload_id = ?",
                (upload_id,),
            )
            self._conn.execute(
                "DELETE FROM publish_journal WHERE upload_id = ?",
                (upload_id,),
            )

    def discard_publish(self, upload_id: str) -> None:
        """Drop a journal row whose file swap never happened."""
        with self._conn:
            self._conn.execute(
                "DELETE FROM publish_journal WHERE upload_id = ?",
                (upload_id,),
            )
