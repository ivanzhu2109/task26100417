"""上传管理器：开启上传、提交分块、查询进度、原子发布。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional, Tuple
from urllib.parse import quote

from .errors import (
    ChunkConflictError,
    InvalidChunkError,
    InvalidNameError,
    UploadAlreadyPublishedError,
    UploadExistsError,
    UploadIncompleteError,
    UploadNotFoundError,
)
from .store import Store, UploadMeta

MAX_CHUNK_SIZE = 4 * 1024 * 1024  # 4 MiB
_COMPARE_WINDOW = 1024 * 1024  # 比对已确认区域时的读取窗口


@dataclass
class Progress:
    """上传进度查询结果。"""

    upload_id: str
    name: str
    total: int
    intervals: List[Tuple[int, int]]
    confirmed_bytes: int
    published: bool


class UploadManager:
    """分块续传与原子发布的主入口。"""

    def __init__(self, root_dir: str) -> None:
        self._root = root_dir
        self._uploads_dir = os.path.join(root_dir, "uploads")
        self._files_dir = os.path.join(root_dir, "files")
        os.makedirs(self._uploads_dir, exist_ok=True)
        os.makedirs(self._files_dir, exist_ok=True)
        self._store = Store(os.path.join(root_dir, "state.db"))
        self._store.open()
        self._recover()

    # ---------- 路径与校验 ----------

    def _staging_path(self, upload_id: str) -> str:
        return os.path.join(self._uploads_dir, quote(upload_id, safe="") + ".part")

    def _target_path(self, name: str) -> str:
        return os.path.join(self._files_dir, name)

    @staticmethod
    def _validate_name(name: str) -> None:
        if (
            not name
            or name in (".", "..")
            or os.sep in name
            or (os.altsep and os.altsep in name)
        ):
            raise InvalidNameError(f"invalid target name: {name!r}")

    def _require_meta(self, upload_id: str) -> UploadMeta:
        meta = self._store.get_meta(upload_id)
        if meta is None:
            raise UploadNotFoundError(upload_id)
        return meta

    # ---------- 崩溃恢复 ----------

    def _recover(self) -> None:
        """完成崩溃前已开始但未确认的发布。"""
        for meta in self._store.list_intents():
            staging = self._staging_path(meta.upload_id)
            if os.path.exists(staging):
                # rename 尚未发生：重做原子替换
                os.replace(staging, self._target_path(meta.name))
                self._fsync_dir(self._files_dir)
            # rename 已发生但结果未落库：仅补记发布结论
            self._store.set_published(meta.upload_id)

    @staticmethod
    def _fsync_dir(path: str) -> None:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    # ---------- 对外接口 ----------

    def open_upload(self, upload_id: str, name: str, total: int) -> UploadMeta:
        self._validate_name(name)
        if not isinstance(total, int) or total < 0:
            raise InvalidChunkError(f"invalid total length: {total!r}")
        meta = self._store.get_meta(upload_id)
        if meta is not None:
            if meta.name != name or meta.total != total:
                raise UploadExistsError(
                    f"upload {upload_id!r} exists with different parameters"
                )
            return meta
        meta = self._store.create_upload(upload_id, name, total)
        staging = self._staging_path(upload_id)
        fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        self._fsync_dir(self._uploads_dir)
        return meta

    def submit_chunk(self, upload_id: str, offset: int, data: bytes) -> Progress:
        meta = self._require_meta(upload_id)
        if meta.published:
            raise UploadAlreadyPublishedError(upload_id)
        if offset < 0 or offset + len(data) > meta.total:
            raise InvalidChunkError(
                f"chunk [{offset}, {offset + len(data)}) out of range [0, {meta.total})"
            )
        if len(data) > MAX_CHUNK_SIZE:
            raise InvalidChunkError("chunk exceeds 4 MiB limit")
        if not data:
            return self.get_progress(upload_id)

        intervals = self._store.get_intervals(upload_id)
        end = offset + len(data)
        staging = self._staging_path(upload_id)
        fd = os.open(staging, os.O_RDWR)
        try:
            # 先校验所有与已确认区域重叠的字节，任一不同则整次拒绝
            for seg_start, seg_end in intervals:
                ov_start = max(offset, seg_start)
                ov_end = min(end, seg_end)
                if ov_start >= ov_end:
                    continue
                pos = ov_start
                while pos < ov_end:
                    n = min(_COMPARE_WINDOW, ov_end - pos)
                    existing = os.pread(fd, n, pos)
                    incoming = data[pos - offset : pos - offset + n]
                    if existing != incoming:
                        raise ChunkConflictError(
                            f"chunk conflicts with confirmed bytes at offset {pos}"
                        )
                    pos += n
            # 校验通过：重叠字节完全相同，整体写入不破坏已确认内容
            view = memoryview(data)
            written = 0
            while written < len(view):
                written += os.pwrite(fd, view[written:], offset + written)
            os.fsync(fd)
        finally:
            os.close(fd)
        self._store.add_interval(upload_id, offset, end)
        return self.get_progress(upload_id)

    def get_progress(self, upload_id: str) -> Progress:
        meta = self._require_meta(upload_id)
        intervals = self._store.get_intervals(upload_id)
        confirmed = sum(e - s for s, e in intervals)
        return Progress(
            upload_id=meta.upload_id,
            name=meta.name,
            total=meta.total,
            intervals=intervals,
            confirmed_bytes=confirmed,
            published=meta.published,
        )

    def publish(self, upload_id: str) -> Progress:
        meta = self._require_meta(upload_id)
        if meta.published:
            # 重复发布：返回原成功结论，不再改动目标
            return self.get_progress(upload_id)
        intervals = self._store.get_intervals(upload_id)
        complete = (meta.total == 0 and not intervals) or intervals == [(0, meta.total)]
        if not complete:
            raise UploadIncompleteError(upload_id)
        staging = self._staging_path(upload_id)
        target = self._target_path(meta.name)
        # 1) 记录发布意图 2) 原子替换 3) 确认发布；崩溃后由 _recover 收尾
        self._store.set_publish_intent(upload_id, True)
        os.replace(staging, target)
        self._fsync_dir(self._files_dir)
        self._store.set_published(upload_id)
        return self.get_progress(upload_id)

    def close(self) -> None:
        if self._store is not None:
            self._store.close()
