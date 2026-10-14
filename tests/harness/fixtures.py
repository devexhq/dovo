"""Loader for the committed, scrubbed provider attempt-log fixtures."""

from __future__ import annotations

from pathlib import Path
from typing import Final

AGENT_FIXTURE_DIR: Final[Path] = Path(__file__).resolve().parents[1] / "fixtures" / "agents"


def load_agent_fixture(provider: str, name: str) -> list[str]:
    """Return the lines of the committed ``<provider>/<name>.jsonl`` attempt-log fixture."""
    return (AGENT_FIXTURE_DIR / provider / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
