"""Parity invariant: each provider's progress mapping table in `docs/agents/schemas.md` matches its mapper's tool table."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

import pytest

from dovo.core.agents.copilot_progress import COPILOT_TARGET_FIELDS, COPILOT_TOOL_KINDS
from dovo.core.agents.models import AgentProgressKind
from dovo.core.agents.registry import PROVIDERS
from tests.lint.astlib import REPO_ROOT

SCHEMAS_PATH: Final = REPO_ROOT / "docs" / "agents" / "schemas.md"
ROW_PATTERN: Final = re.compile(r"^\| `(?P<kind>[a-z]+)` \| (?P<tools>[^|]+) \| (?P<target>[^|]+) \|", re.MULTILINE)
TARGET_FIELD_PATTERN: Final = re.compile(r"`arguments\.([a-z]+)`")
TOOL_PATTERN: Final = re.compile(r"`([a-z_]+)`")


@dataclass(frozen=True)
class ProgressParityEntry:
    """One provider's progress-mapping specification: its schemas.md heading and the constants the mapper reads."""

    provider: str
    heading: str
    tool_kinds: Mapping[str, AgentProgressKind]
    target_fields: Mapping[str, str]


PROGRESS_PARITY_REGISTRY: Final[tuple[ProgressParityEntry, ...]] = (
    ProgressParityEntry(
        provider="copilot",
        heading="### Copilot progress mapping",
        tool_kinds=COPILOT_TOOL_KINDS,
        target_fields=COPILOT_TARGET_FIELDS,
    ),
)
COPILOT_ENTRY: Final = PROGRESS_PARITY_REGISTRY[0]


def _documented_tools_by_kind(schemas_text: str, heading: str) -> dict[str, set[str]]:
    """Return the tool names listed per kind in the table under the given heading."""
    section = schemas_text.split(heading, 1)[1].split("\n#", 1)[0]
    documented: dict[str, set[str]] = {}
    for row in ROW_PATTERN.finditer(section):
        documented[row["kind"]] = set(TOOL_PATTERN.findall(row["tools"]))

    return documented


def _documented_target_field_by_kind(schemas_text: str, heading: str) -> dict[str, str]:
    """Return the leading `arguments.<field>` named in the Target column per kind under the given heading."""
    section = schemas_text.split(heading, 1)[1].split("\n#", 1)[0]
    documented: dict[str, str] = {}
    for row in ROW_PATTERN.finditer(section):
        field = TARGET_FIELD_PATTERN.search(row["target"])
        if field is not None:
            documented[row["kind"]] = field.group(1)

    return documented


def _mapped_target_field_by_kind(entry: ProgressParityEntry) -> dict[str, str]:
    """Return the entry's target_fields grouped by kind value."""
    return {entry.tool_kinds[tool].value: field for tool, field in entry.target_fields.items()}


def _mapped_tools_by_kind(entry: ProgressParityEntry) -> dict[str, set[str]]:
    """Return the entry's tool_kinds grouped by kind value."""
    grouped: dict[str, set[str]] = defaultdict(set)
    for tool_name, kind in entry.tool_kinds.items():
        grouped[kind.value].add(tool_name)

    return dict(grouped)


class ProgressMappingParityTests:
    @pytest.mark.parametrize("entry", PROGRESS_PARITY_REGISTRY, ids=lambda entry: entry.provider)
    def test_schemas_table_matches_the_mapper_tool_kinds(self, entry: ProgressParityEntry) -> None:
        """[tier-1/unit] ProgressParityEntry.tool_kinds: the (Kind, Tool names) rows under entry.heading in docs/agents/schemas.md are non-empty and equal entry.tool_kinds grouped by kind value, and set(entry.target_fields) <= set(entry.tool_kinds)."""
        documented = _documented_tools_by_kind(SCHEMAS_PATH.read_text(encoding="utf-8"), entry.heading)

        assert documented
        assert documented == _mapped_tools_by_kind(entry)
        assert set(entry.target_fields) <= set(entry.tool_kinds)

    @pytest.mark.parametrize("entry", PROGRESS_PARITY_REGISTRY, ids=lambda entry: entry.provider)
    def test_schemas_target_column_matches_the_mapper_target_fields(self, entry: ProgressParityEntry) -> None:
        """[tier-1/unit] ProgressParityEntry.target_fields: the leading `arguments.<field>` of each row's Target column under entry.heading is non-empty and equals entry.target_fields grouped by kind value."""
        documented = _documented_target_field_by_kind(SCHEMAS_PATH.read_text(encoding="utf-8"), entry.heading)

        assert documented
        assert documented == _mapped_target_field_by_kind(entry)

    def test_registry_covers_every_provider_with_a_progress_mapper(self) -> None:
        """[tier-1/unit] PROGRESS_PARITY_REGISTRY: {entry.provider for entry in registry} == {token for token, spec in PROVIDERS.items() if spec.progress_mapper is not None}."""
        mapped_tokens = {token for token, spec in PROVIDERS.items() if spec.progress_mapper is not None}

        assert mapped_tokens
        assert {entry.provider for entry in PROGRESS_PARITY_REGISTRY} == mapped_tokens

    def test_target_parser_flags_a_row_that_drifts_from_the_mapper(self) -> None:
        """[tier-1/unit] _documented_target_field_by_kind: a table under the Copilot heading naming `arguments.command` for search parses to a mapping that differs from _mapped_target_field_by_kind(copilot entry)."""
        drifted = (
            f"{COPILOT_ENTRY.heading}\n\n| Kind | Tool names | Target |\n| --- | --- | --- |\n"
            "| `search` | `rg`, `grep`, `glob` | `arguments.command` |\n"
        )

        assert _documented_target_field_by_kind(drifted, COPILOT_ENTRY.heading) != _mapped_target_field_by_kind(
            COPILOT_ENTRY
        )

    def test_table_parser_flags_a_row_that_drifts_from_the_mapper(self) -> None:
        """[tier-1/unit] _documented_tools_by_kind: a table under the Copilot heading listing `glob` under read instead of search parses to a mapping that differs from _mapped_tools_by_kind(copilot entry)."""
        drifted = (
            f"{COPILOT_ENTRY.heading}\n\n| Kind | Tool names | Target |\n| --- | --- | --- |\n"
            "| `read` | `view`, `glob` | x |\n"
        )

        assert _documented_tools_by_kind(drifted, COPILOT_ENTRY.heading) != _mapped_tools_by_kind(COPILOT_ENTRY)
