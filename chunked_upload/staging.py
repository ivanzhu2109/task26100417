"""On-disk layout: staging files, published files, atomic replacement.

Layout under the store root::

    <root>/state.db          SQLite state (owned by repository.py)
    <root>/staging/<token>   per-upload staging file with confirmed bytes
    <root>/files/<name>      published files

Publishing is an atomic os.replace() of the staging file onto the target,
journaled in SQLite so a killed process can be reconciled on reopen.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import IO

from .errors import InvalidNameError


def validate_name(name: str) -> str:
    """Ensure the target name is a plain file name, return it."""
    if (
        not name
        or name in (".", "..")
        or "/" in name
        or "\\" in name
        or "\0" in name
    ):
        raise InvalidNameError(f"unsafe target file name: {name!r}")
    return name


def staging_token(upload_id: str) -> str:
    """Derive a filesystem-safe staging file name from an upload id."""
    return hashlib.sha256(upload_id.encode("utf-8")).hexdigest() + ".part"


class FileLayout:
    """Resolves and manages the on-disk paths of a store."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.staging_dir = self.root / "staging"
        self.files_dir = self.root / "files"

    def ensure_dirs(self) -> None:
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)

    def staging_path(self, token: str) -> Path:
        return self.staging_dir / token

    def target_path(self, name: str) -> Path:
        return self.files_dir / validate_name(name)

    def open_staging(self, token: str) -> IO[bytes]:
        """Open (creating if needed) the staging file for random writes."""
        return open(self.staging_path(token), "r+b" if self.staging_path(token).exists() else "w+b")

    def atomic_publish(self, staging: Path, target: Path) -> None:
        """Atomically replace target with the staging file."""
        os.replace(staging, target)
        dir_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
