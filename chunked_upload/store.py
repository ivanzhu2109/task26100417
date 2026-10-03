"""SQLite 状态存储层：上传元数据与已确认字节区间。"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import List, Optional, Tuple

_SCHEMA = """
CREATE TABLE IF NOT EXISTS uploads (
    upload_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    total INTEGER NOT NULL,
    published INTEGER NOT NULL DEFAULT 0,
    publish_intent INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS intervals (
    upload_id TEXT NOT NULL,
    start INTEGER NOT NULL,
    end INTEGER NOT NULL,
    PRIMARY KEY (upload_id, start)
);
"""


@dataclass
class UploadMeta:
    """单个上传的元数据。"""

    upload_id: str
    name: str
    total: int
    published: bool = False
    publish_intent: bool = False


class Store:
    """封装 SQLite 中的上传状态。"""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def open(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self._db_path)), exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.executescript(_SCHEMA)
        conn.commit()
        self._conn = conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("store is not open")
        return self._conn

    @staticmethod
    def _row_to_meta(row: Tuple) -> UploadMeta:
        return UploadMeta(
            upload_id=row[0],
            name=row[1],
            total=row[2],
            published=bool(row[3]),
            publish_intent=bool(row[4]),
        )

    def get_meta(self, upload_id: str) -> Optional[UploadMeta]:
        conn = self._require_conn()
        row = conn.execute(
            "SELECT upload_id, name, total, published, publish_intent"
            " FROM uploads WHERE upload_id = ?",
            (upload_id,),
        ).fetchone()
        return self._row_to_meta(row) if row else None

    def create_upload(self, upload_id: str, name: str, total: int) -> UploadMeta:
        conn = self._require_conn()
        with conn:
            conn.execute(
                "INSERT INTO uploads (upload_id, name, total) VALUES (?, ?, ?)",
                (upload_id, name, total),
            )
        return UploadMeta(upload_id=upload_id, name=name, total=total)

    def list_intents(self) -> List[UploadMeta]:
        conn = self._require_conn()
        rows = conn.execute(
            "SELECT upload_id, name, total, published, publish_intent"
            " FROM uploads WHERE publish_intent != 0"
        ).fetchall()
        return [self._row_to_meta(r) for r in rows]

    def set_publish_intent(self, upload_id: str, value: bool) -> None:
        conn = self._require_conn()
        with conn:
            conn.execute(
                "UPDATE uploads SET publish_intent = ? WHERE upload_id = ?",
                (1 if value else 0, upload_id),
            )

    def set_published(self, upload_id: str) -> None:
        conn = self._require_conn()
        with conn:
            conn.execute(
                "UPDATE uploads SET published = 1, publish_intent = 0"
                " WHERE upload_id = ?",
                (upload_id,),
            )

    def get_intervals(self, upload_id: str) -> List[Tuple[int, int]]:
        conn = self._require_conn()
        rows = conn.execute(
            "SELECT start, end FROM intervals WHERE upload_id = ? ORDER BY start",
            (upload_id,),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def add_interval(self, upload_id: str, start: int, end: int) -> List[Tuple[int, int]]:
        """合并写入新区间，返回合并后的完整区间列表（单个事务）。"""
        conn = self._require_conn()
        with conn:
            rows = conn.execute(
                "SELECT start, end FROM intervals WHERE upload_id = ? ORDER BY start",
                (upload_id,),
            ).fetchall()
            merged: List[Tuple[int, int]] = []
            new_start, new_end = start, end
            for cur_start, cur_end in rows:
                if cur_end < new_start:
                    merged.append((cur_start, cur_end))
                elif new_end < cur_start:
                    merged.append((new_start, new_end))
                    new_start, new_end = cur_start, cur_end
                else:
                    new_start = min(new_start, cur_start)
                    new_end = max(new_end, cur_end)
            merged.append((new_start, new_end))
            conn.execute("DELETE FROM intervals WHERE upload_id = ?", (upload_id,))
            conn.executemany(
                "INSERT INTO intervals (upload_id, start, end) VALUES (?, ?, ?)",
                [(upload_id, s, e) for s, e in merged],
            )
        return merged
