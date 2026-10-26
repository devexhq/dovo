"""Entry-point invariant: the installed `dovo` script runs `run_cli`'s top-level error handler."""

from __future__ import annotations

import tomllib
from typing import Final

from tests.lint.astlib import REPO_ROOT

PYPROJECT_PATH: Final = REPO_ROOT / "pyproject.toml"


class EntryPointTests:
    """`[project.scripts]` in pyproject.toml."""

    def test_dovo_script_targets_run_cli(self) -> None:
        """Map exactly the `dovo` script to `dovo.cli.cli:run_cli` in pyproject.toml [project.scripts]."""
        data = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))

        assert data["project"]["scripts"] == {"dovo": "dovo.cli.cli:run_cli"}
