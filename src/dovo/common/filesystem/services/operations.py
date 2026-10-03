from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def compute_content_checksum(content: str) -> str:
    """Return SHA-256 hex digest of UTF-8-encoded content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def delete_file(path: Path) -> bool:
    """Delete a file if it exists. Returns True if the file existed before deletion."""
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write JSON atomically with indent=2, UTF-8, and trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        tmp_path.replace(path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, text: str) -> None:
    """Write text content atomically with UTF-8 encoding."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        tmp_path.replace(path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise
