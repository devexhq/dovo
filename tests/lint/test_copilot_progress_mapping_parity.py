"""Parity invariant: the Copilot progress mapping table in `docs/agents/schemas.md` matches the mapper's tool table."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Final

from dovo.core.agents.services.map_progress import COPILOT_TARGET_FIELDS, COPILOT_TOOL_KINDS
from tests.lint.astlib import REPO_ROOT

SCHEMAS_PATH: Final = REPO_ROOT / "docs" / "agents" / "schemas.md"
TABLE_HEADING: Final = "### Copilot progress mapping"
ROW_PATTERN: Final = re.compile(r"^\| `(?P<kind>[a-z]+)` \| (?P<tools>[^|]+) \| (?P<target>[^|]+) \|", re.MULTILINE)
TARGET_FIELD_PATTERN: Final = re.compile(r"`arguments\.([a-z]+)`")
TOOL_PATTERN: Final = re.compile(r"`([a-z_]+)`")


def _documented_tools_by_kind(schemas_text: str) -> dict[str, set[str]]:
    """Return the tool names listed per kind in the table under the mapping heading."""
    section = schemas_text.split(TABLE_HEADING, 1)[1].split("\n#", 1)[0]
    documented: dict[str, set[str]] = {}
    for row in ROW_PATTERN.finditer(section):
        documented[row["kind"]] = set(TOOL_PATTERN.findall(row["tools"]))

    return documented


def _documented_target_field_by_kind(schemas_text: str) -> dict[str, str]:
    """Return the leading `arguments.<field>` named in the Target column per kind."""
    section = schemas_text.split(TABLE_HEADING, 1)[1].split("\n#", 1)[0]
    documented: dict[str, str] = {}
    for row in ROW_PATTERN.finditer(section):
        field = TARGET_FIELD_PATTERN.search(row["target"])
        if field is not None:
            documented[row["kind"]] = field.group(1)

    return documented


def _mapped_target_field_by_kind() -> dict[str, str]:
    """Return COPILOT_TARGET_FIELDS grouped by kind value."""
    return {COPILOT_TOOL_KINDS[tool].value: field for tool, field in COPILOT_TARGET_FIELDS.items()}


def _mapped_tools_by_kind() -> dict[str, set[str]]:
    """Return COPILOT_TOOL_KINDS grouped by kind value."""
    grouped: dict[str, set[str]] = defaultdict(set)
    for tool_name, kind in COPILOT_TOOL_KINDS.items():
        grouped[kind.value].add(tool_name)

    return dict(grouped)


class CopilotProgressMappingParityTests:
    def test_schemas_table_matches_the_mapper_tool_kinds(self) -> None:
        """[tier-1/unit] COPILOT_TOOL_KINDS: the (Kind, Tool names) rows of the 'Copilot progress mapping' table in docs/agents/schemas.md equal COPILOT_TOOL_KINDS grouped by kind, and every tool outside apply_patch has a target field."""
        documented = _documented_tools_by_kind(SCHEMAS_PATH.read_text(encoding="utf-8"))

        assert documented
        assert documented == _mapped_tools_by_kind()
        assert set(COPILOT_TOOL_KINDS) - {"apply_patch"} == set(COPILOT_TARGET_FIELDS)

    def test_schemas_target_column_matches_the_mapper_target_fields(self) -> None:
        """[tier-1/unit] COPILOT_TARGET_FIELDS: the leading `arguments.<field>` of each row's Target column in the 'Copilot progress mapping' table equals the field the mapper reads for that kind."""
        documented = _documented_target_field_by_kind(SCHEMAS_PATH.read_text(encoding="utf-8"))

        assert documented
        assert documented == _mapped_target_field_by_kind()

    def test_target_parser_flags_a_row_that_drifts_from_the_mapper(self) -> None:
        """[tier-1/unit] _documented_target_field_by_kind: a table naming `arguments.command` for search parses to a mapping that differs from COPILOT_TARGET_FIELDS grouped by kind."""
        drifted = (
            f"{TABLE_HEADING}\n\n| Kind | Tool names | Target |\n| --- | --- | --- |\n"
            "| `search` | `rg`, `grep`, `glob` | `arguments.command` |\n"
        )

        assert _documented_target_field_by_kind(drifted) != _mapped_target_field_by_kind()

    def test_table_parser_flags_a_row_that_drifts_from_the_mapper(self) -> None:
        """[tier-1/unit] _documented_tools_by_kind: a table listing `glob` under read instead of search parses to a mapping that differs from COPILOT_TOOL_KINDS grouped by kind."""
        drifted = (
            f"{TABLE_HEADING}\n\n| Kind | Tool names | Target |\n| --- | --- | --- |\n| `read` | `view`, `glob` | x |\n"
        )

        assert _documented_tools_by_kind(drifted) != _mapped_tools_by_kind()
