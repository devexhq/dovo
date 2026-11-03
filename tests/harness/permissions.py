"""Permission-denied doubles that behave the same for every user, including root, which ignores permission bits."""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import IO

import pytest

_CREATING_MODES = frozenset("wax")


def _permission_denied(path: Path) -> PermissionError:
    """Return the PermissionError the OS raises for path, e.g. "[Errno 13] Permission denied: '<path>'"."""
    return PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(path))


def deny_entry_creation(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    """Make creating a directory or file directly inside directory fail as it would in a read-only directory.

    Existing entries stay writable and their own children are unaffected, as with a real chmod 0o555 directory.
    """
    real_mkdir = os.mkdir
    real_open = Path.open

    def mkdir(path: str | os.PathLike[str], mode: int = 0o777, *, dir_fd: int | None = None) -> None:
        target = Path(path)
        if target.parent == directory:
            if target.exists():
                raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(target))
            raise _permission_denied(target)
        real_mkdir(path, mode, dir_fd=dir_fd)

    def open_path(
        self: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> IO[str] | IO[bytes]:
        if self.parent == directory and not self.exists() and _CREATING_MODES & set(mode):
            raise _permission_denied(self)
        return real_open(self, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(os, "mkdir", mkdir)
    monkeypatch.setattr(Path, "open", open_path)


def deny_read(monkeypatch: pytest.MonkeyPatch, file: Path) -> None:
    """Make opening file fail as it would for a chmod 0o000 file."""
    real_open = Path.open

    def open_path(
        self: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> IO[str] | IO[bytes]:
        if self == file:
            raise _permission_denied(self)
        return real_open(self, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(Path, "open", open_path)
