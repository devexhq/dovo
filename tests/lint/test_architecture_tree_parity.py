"""Parity invariant: every package directory under the four source layers appears in the architecture.md layers tree."""

from __future__ import annotations

import re
from typing import Final

import pytest

from tests.lint.astlib import REPO_ROOT, SRC_ROOT

ARCHITECTURE_PATH: Final = REPO_ROOT / "docs" / "agents" / "architecture.md"
LAYERS: Final[tuple[str, ...]] = ("cli", "core", "engine", "common")
NON_PACKAGE_DIRS: Final[frozenset[str]] = frozenset({"__pycache__", "docs"})
LAYER_HEADER: Final = re.compile(r"^src/dovo/(\w+)/")
PACKAGE_NAMES: Final = re.compile(r"^  ((?:\w+/\s?)+)")


def _documented_packages() -> dict[str, set[str]]:
    """Return the package names listed for each layer in the first fenced block of the Layers section."""
    section = ARCHITECTURE_PATH.read_text(encoding="utf-8").split("## Layers", 1)[1]
    block = section.split("```", 2)[1]
    documented: dict[str, set[str]] = {}
    current = ""
    for line in block.splitlines():
        header = LAYER_HEADER.match(line)
        if header:
            current = header.group(1)
            documented[current] = set()
            continue
        names = PACKAGE_NAMES.match(line)
        if names and current:
            documented[current].update(name.rstrip("/") for name in names.group(1).split())
    return documented


def _packages_on_disk(layer: str) -> set[str]:
    """Return the package directory names directly under a source layer."""
    return {
        entry.name for entry in (SRC_ROOT / layer).iterdir() if entry.is_dir() and entry.name not in NON_PACKAGE_DIRS
    }


class ArchitectureTreeParityTests:
    """The architecture.md layers tree must list exactly the packages on disk."""

    @pytest.mark.parametrize("layer", [pytest.param(layer, id=layer) for layer in LAYERS])
    def test_layer_lists_exactly_the_packages_on_disk(self, layer: str) -> None:
        """Documented package names for a layer equal its package directories."""
        documented = _documented_packages()[layer]

        assert documented == _packages_on_disk(layer)
