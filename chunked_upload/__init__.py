"""Chunked resumable uploads with atomic publish.

Usage::

    from chunked_upload import UploadStore

    store = UploadStore("/var/lib/uploads")
    store.open_upload("up-1", "big.bin", length=10_000_000)
    store.submit_chunk("up-1", 0, b"...")
    print(store.progress("up-1").intervals)
    store.publish("up-1")
"""

from .errors import (
    ChunkConflictError,
    ChunkTooLargeError,
    IncompleteUploadError,
    InvalidChunkError,
    InvalidNameError,
    UploadClosedError,
    UploadError,
    UploadExistsError,
    UploadNotFoundError,
)
from .models import MAX_CHUNK_SIZE, MAX_FILE_LENGTH, Progress, PublishResult, UploadInfo
from .store import UploadStore

__all__ = [
    "UploadStore",
    "Progress",
    "PublishResult",
    "UploadInfo",
    "MAX_CHUNK_SIZE",
    "MAX_FILE_LENGTH",
    "UploadError",
    "UploadNotFoundError",
    "UploadExistsError",
    "InvalidNameError",
    "InvalidChunkError",
    "ChunkTooLargeError",
    "ChunkConflictError",
    "UploadClosedError",
    "IncompleteUploadError",
]
