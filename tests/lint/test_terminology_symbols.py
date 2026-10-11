"""Parity invariant: every type named in the architecture.md Terminology table is defined in src/dovo."""

from __future__ import annotations

import re
from typing import Final

import pytest

from tests.lint.astlib import REPO_ROOT, SRC_ROOT, collect_python_files

ARCHITECTURE_PATH: Final = REPO_ROOT / "docs" / "agents" / "architecture.md"
BACKTICKED_TYPE: Final = re.compile(r"`([A-Z][A-Za-z0-9]+)`")
CLASS_DEFINITION: Final = re.compile(r"^class (\w+)", re.MULTILINE)


def _documented_types() -> list[str]:
    """Return the backticked CamelCase names in the Terminology section, in document order."""
    section = ARCHITECTURE_PATH.read_text(encoding="utf-8").split("## Terminology", 1)[1].split("\n## ", 1)[0]
    return list(dict.fromkeys(BACKTICKED_TYPE.findall(section)))


def _defined_classes() -> set[str]:
    """Return the name of every class defined under src/dovo."""
    return {
        name
        for file_path in collect_python_files(SRC_ROOT)
        for name in CLASS_DEFINITION.findall(file_path.read_text(encoding="utf-8"))
    }


class TerminologySymbolTests:
    """The Terminology table may only name types that exist."""

    def test_terminology_table_names_at_least_one_type(self) -> None:
        """The parser finds types in the table, so the per-type test cannot pass vacuously."""
        assert "BlueprintDefinition" in _documented_types()

    @pytest.mark.parametrize("type_name", [pytest.param(name, id=name) for name in _documented_types()])
    def test_documented_type_is_defined_in_source(self, type_name: str) -> None:
        """Each backticked type in the Terminology table is a class under src/dovo."""
        assert type_name in _defined_classes()
