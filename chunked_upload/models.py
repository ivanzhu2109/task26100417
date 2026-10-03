"""Public data types returned by the upload store."""

from __future__ import annotations

from dataclasses import dataclass

#: Maximum size of a single submitted chunk (4 MiB).
MAX_CHUNK_SIZE = 4 * 1024 * 1024

#: Maximum supported declared file length (2 GiB).
MAX_FILE_LENGTH = 2 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class UploadInfo:
    """Identity and parameters of an upload session."""

    upload_id: str
    name: str
    length: int


@dataclass(frozen=True)
class Progress:
    """Snapshot of an upload's confirmed state.

    ``intervals`` holds the confirmed byte ranges as sorted, disjoint,
    non-adjacent ``(start, end)`` pairs (end exclusive).  ``confirmed`` is
    the number of confirmed bytes; holes never count towards it.
    """

    upload_id: str
    name: str
    length: int
    intervals: tuple[tuple[int, int], ...]
    confirmed: int
    published: bool

    @property
    def complete(self) -> bool:
        """True when every declared byte is confirmed."""
        return self.confirmed == self.length


@dataclass(frozen=True)
class PublishResult:
    """Outcome of a successful publish."""

    upload_id: str
    name: str
    length: int
    path: str
