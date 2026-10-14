"""Loader for the committed, scrubbed provider attempt-log fixtures."""

from __future__ import annotations

from pathlib import Path
from typing import Final

COPILOT_FIXTURE_DIR: Final[Path] = Path(__file__).resolve().parents[1] / "fixtures" / "agents" / "copilot"


def load_copilot_fixture(name: str) -> list[str]:
    """Return the lines of the committed Copilot attempt-log fixture ``<name>.jsonl``."""
    return (COPILOT_FIXTURE_DIR / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
