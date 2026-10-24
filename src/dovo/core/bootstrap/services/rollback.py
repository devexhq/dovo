"""Snapshot and restore the project's .dovo tree around an automatic initialization attempt."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from dovo.common.constants import BOOTSTRAP_META_REL
from dovo.common.filesystem import Filesystem
from dovo.core.bootstrap.models import DovoTreeSnapshot

RESTORED_FILES: tuple[Path, ...] = (Path(".gitignore"), Path(BOOTSTRAP_META_REL), Path("config.json"))


def snapshot_dovo_tree(dovo_dir: Path) -> DovoTreeSnapshot:
    """Record which paths exist under dovo_dir and the bytes of the files initialization may rewrite in place."""
    if not dovo_dir.is_dir():
        return DovoTreeSnapshot(dovo_dir=dovo_dir, existed=False, paths=frozenset(), file_bytes={})
    root = dovo_dir.resolve()
    paths: set[Path] = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        parent = Path(dirpath).relative_to(root)
        paths.update(parent / name for name in (*dirnames, *filenames))
    file_bytes = {rel: (root / rel).read_bytes() for rel in RESTORED_FILES if (root / rel).is_file()}
    return DovoTreeSnapshot(dovo_dir=dovo_dir, existed=True, paths=frozenset(paths), file_bytes=file_bytes)


def restore_dovo_tree(snapshot: DovoTreeSnapshot) -> None:
    """Remove every path created since snapshot and rewrite the recorded files; raise OSError when that fails."""
    dovo_dir = snapshot.dovo_dir
    if not snapshot.existed:
        if dovo_dir.is_dir() and not dovo_dir.is_symlink():
            shutil.rmtree(dovo_dir)
        return
    root = dovo_dir.resolve()
    _remove_new_paths(root, snapshot.paths)
    for rel, data in snapshot.file_bytes.items():
        Filesystem.atomic_write_bytes(root / rel, data)


def _remove_new_paths(root: Path, known: frozenset[Path]) -> None:
    """Remove each path under root absent from known, without descending into the removed directories."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        parent = Path(dirpath)
        rel_parent = parent.relative_to(root)
        new_names = {name for name in (*dirnames, *filenames) if rel_parent / name not in known}
        for name in new_names:
            _remove_path(parent / name)
        dirnames[:] = [name for name in dirnames if name not in new_names]


def _remove_path(path: Path) -> None:
    """Remove path: a real directory with its contents, or a file or symlink itself."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()
