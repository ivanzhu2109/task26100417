"""Public entry point: the UploadStore class."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional, Union

from .errors import (
    ChunkConflictError,
    ChunkTooLargeError,
    IncompleteUploadError,
    InvalidChunkError,
    UploadClosedError,
)
from .models import MAX_CHUNK_SIZE, MAX_FILE_LENGTH, Progress, PublishResult, UploadInfo
from .repository import Repository
from .staging import FileLayout, staging_token, validate_name

#: Named points where an optional fault hook is invoked.  Tests use this to
#: simulate a process kill (os._exit) mid-operation.
FAULT_POINTS = (
    "chunk.after_write",
    "chunk.before_commit",
    "publish.after_journal",
    "publish.after_replace",
    "publish.before_commit",
)


class UploadStore:
    """Resumable chunked uploads with atomic publish.

    All calls are serialized by the caller; a single process owns the store.
    Reopening the same root directory recovers interrupted operations
    automatically.
    """

    def __init__(
        self,
        root: Union[str, Path],
        fault_hook: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._layout = FileLayout(Path(root))
        self._layout.ensure_dirs()
        self._repo = Repository(self._layout.root / "state.db")
        self._fault_hook = fault_hook
        self._recover()

    def close(self) -> None:
        self._repo.close()

    def __enter__(self) -> "UploadStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _fault(self, point: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(point)

    def _recover(self) -> None:
        """Reconcile interrupted publishes found in the journal."""
        for row in self._repo.pending_publishes():
            staging = self._layout.staging_path(row["staging"])
            if staging.exists():
                # os.replace never ran: the target was not touched.
                self._repo.discard_publish(row["upload_id"])
            else:
                # os.replace already swapped the target atomically.
                self._repo.finish_publish(row["upload_id"])

    # -- public API ---------------------------------------------------------

    def open_upload(self, upload_id: str, name: str, length: int) -> UploadInfo:
        """Start or reattach to an upload (idempotent on same parameters)."""
        validate_name(name)
        if not 0 <= length <= MAX_FILE_LENGTH:
            raise ValueError(f"length out of supported range: {length}")
        token = staging_token(upload_id)
        self._repo.create_upload(upload_id, name, length, token)
        return UploadInfo(upload_id=upload_id, name=name, length=length)

    def submit_chunk(self, upload_id: str, offset: int, data: bytes) -> None:
        """Confirm the bytes of ``data`` at ``offset``.

        Raises ChunkConflictError (applying nothing) if any byte overlapping
        an already confirmed region differs.
        """
        row = self._repo.get_upload(upload_id)
        if row["published"]:
            raise UploadClosedError(f"upload {upload_id!r} is already published")
        length = row["length"]
        if len(data) > MAX_CHUNK_SIZE:
            raise ChunkTooLargeError(
                f"chunk of {len(data)} bytes exceeds {MAX_CHUNK_SIZE}"
            )
        if offset < 0 or offset > length or offset + len(data) > length:
            raise InvalidChunkError(
                f"chunk [{offset}, {offset + len(data)}) outside [0, {length})"
            )
        if not data:
            return
        end = offset + len(data)
        staging = self._layout.staging_path(row["staging"])
        overlaps = [
            (s, e)
            for s, e in self._repo.get_intervals(upload_id)
            if s < end and e > offset
        ]
        if overlaps:
            with open(staging, "rb") as fh:
                for s, e in overlaps:
                    lo, hi = max(s, offset), min(e, end)
                    fh.seek(lo)
                    confirmed = fh.read(hi - lo)
                    incoming = data[lo - offset: hi - offset]
                    if confirmed != incoming:
                        raise ChunkConflictError(
                            f"chunk contradicts confirmed bytes in [{lo}, {hi})"
                        )
        with self._layout.open_staging(row["staging"]) as fh:
            fh.seek(offset)
            fh.write(data)
        self._fault("chunk.after_write")
        self._fault("chunk.before_commit")
        self._repo.add_coverage(upload_id, offset, end)

    def progress(self, upload_id: str) -> Progress:
        """Return the exact confirmed intervals and published flag."""
        row = self._repo.get_upload(upload_id)
        intervals = tuple(self._repo.get_intervals(upload_id))
        confirmed = sum(e - s for s, e in intervals)
        return Progress(
            upload_id=upload_id,
            name=row["name"],
            length=row["length"],
            intervals=intervals,
            confirmed=confirmed,
            published=bool(row["published"]),
        )

    def publish(self, upload_id: str) -> PublishResult:
        """Atomically swap the target file to the confirmed content.

        Retrying a completed publish returns the original result without
        touching the target.
        """
        row = self._repo.get_upload(upload_id)
        name = row["name"]
        target = self._layout.target_path(name)
        if row["published"]:
            return PublishResult(
                upload_id=upload_id, name=name,
                length=row["length"], path=str(target),
            )
        intervals = self._repo.get_intervals(upload_id)
        if sum(e - s for s, e in intervals) != row["length"]:
            raise IncompleteUploadError(
                f"upload {upload_id!r} has unconfirmed holes"
            )
        token = row["staging"]
        staging = self._layout.staging_path(token)
        with self._layout.open_staging(token) as fh:
            fh.flush()
            os.fsync(fh.fileno())
        self._repo.journal_publish(upload_id, name, token)
        self._fault("publish.after_journal")
        self._layout.atomic_publish(staging, target)
        self._fault("publish.after_replace")
        self._fault("publish.before_commit")
        self._repo.finish_publish(upload_id)
        return PublishResult(
            upload_id=upload_id, name=name,
            length=row["length"], path=str(target),
        )
