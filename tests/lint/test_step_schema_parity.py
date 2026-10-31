"""Parity: the spec tables in `docs/reference/step-schema.md` and `docs/cli/session.md` match their source models."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest
import typer.core
import typer.main

from dovo.cli.session.app import session_app
from dovo.core.catalog.definitions.step import ArtifactPublishSpec, InteractionSpec, LoopStepBlock, StepDefinition
from dovo.core.interaction import Interaction, InteractionAnswer, InteractionRequest
from tests.lint.astlib import REPO_ROOT

STEP_SCHEMA_DOC: Final = REPO_ROOT / "docs" / "reference" / "step-schema.md"
SESSION_DOC: Final = REPO_ROOT / "docs" / "cli" / "session.md"
_STEP_HEADING: Final = "## `StepDefinition` Fields"
_HEADING: Final = "## `artifacts` Entries"
_LOOP_HEADING: Final = "## `LoopStepBlock`"
_INTERACTION_HEADING: Final = "## `interaction` Block"
_OUTPUTS_HEADING: Final = "### Ask step outputs"
_RESUME_HEADING: Final = "## `dovo session resume`"
_FLAG_RE: Final = re.compile(r"^\|\s*`(?P<flag>--[a-z-]+)", re.MULTILINE)
_OUTPUT_ROW_RE: Final = re.compile(r"^\|\s*(?P<keys>`[a-z_]+`(?:,\s*`[a-z_]+`)*)\s*\|", re.MULTILINE)
_ROW_RE: Final = re.compile(r"^\|\s*`(?P<field>[a-z_]+)`\s*\|[^|]*\|\s*(?P<required>[^|]*?)\s*\|", re.MULTILINE)


def _section(path: Path, heading: str, end: str) -> str:
    """Return the text of `heading` up to the next `end` marker."""
    return path.read_text(encoding="utf-8").split(heading, 1)[1].split(end, 1)[0]


def _field_rows(section: str) -> dict[str, bool]:
    """Map each documented field in a spec table to whether the table marks it required."""
    return {match["field"]: match["required"] == "**Yes**" for match in _ROW_RE.finditer(section)}


def _documented_rows() -> dict[str, bool]:
    """Map each documented `artifacts` entry field to whether the table marks it required."""
    return _field_rows(_section(STEP_SCHEMA_DOC, _HEADING, "\n---"))


def _standard_step_rows() -> dict[str, bool]:
    """Map each documented standard-step field to whether the table marks it required."""
    return _field_rows(_section(STEP_SCHEMA_DOC, _STEP_HEADING, "\n---"))


def _interaction_rows() -> dict[str, bool]:
    """Map each field of the `interaction` table to whether it is marked required."""
    section = _section(STEP_SCHEMA_DOC, _INTERACTION_HEADING, "| Mode |")
    return _field_rows(section)


def _documented_output_keys() -> set[str]:
    """Return the keys named in the Ask step outputs table."""
    section = _section(STEP_SCHEMA_DOC, _OUTPUTS_HEADING, "\n\nRead them")
    return {key.strip("` ") for match in _OUTPUT_ROW_RE.finditer(section) for key in match["keys"].split(",")}


def _documented_resume_flags() -> set[str]:
    """Return the flags listed in the `dovo session resume` Options table."""
    section = _section(SESSION_DOC, _RESUME_HEADING, "### Behavior")
    return set(_FLAG_RE.findall(section))


def _resume_command_flags() -> set[str]:
    """Return every long option declared on the `resume` command, without `--help`."""
    command = typer.main.get_command(session_app)
    assert isinstance(command, typer.core.TyperGroup)
    resume = command.commands["resume"]
    return {opt for param in resume.params for opt in param.opts if opt.startswith("--")} - {"--help"}


class StepSchemaParityTests:
    """The `artifacts` Entries table must agree with `ArtifactPublishSpec` field for field."""

    def test_documented_fields_match_artifact_publish_spec_fields(self) -> None:
        documented = _documented_rows()

        assert documented
        assert set(documented) == set(ArtifactPublishSpec.model_fields)

    def test_documented_required_flags_match_artifact_publish_spec(self) -> None:
        """Mark a field's Required cell `**Yes**` exactly when the model field has no default."""
        expected = {name: field.is_required() for name, field in ArtifactPublishSpec.model_fields.items()}

        assert _documented_rows() == expected

    def test_documented_step_fields_match_step_definition_fields(self) -> None:
        expected = {
            field.serialization_alias or name: field.is_required()
            for name, field in StepDefinition.model_fields.items()
        }

        documented = _standard_step_rows()

        assert "max_files" in documented
        assert documented == expected

    def test_documented_loop_fields_match_loop_step_block_fields(self) -> None:
        expected = {name: field.is_required() for name, field in LoopStepBlock.model_fields.items()}

        documented = _field_rows(_section(STEP_SCHEMA_DOC, _LOOP_HEADING, "\n---"))

        assert "name" in documented
        assert documented == expected


class InteractionSchemaParityTests:
    """The `interaction` table, Ask step outputs and resume flags must agree with their sources."""

    def test_interaction_fields_and_required_flags_match_interaction_spec(self) -> None:
        expected = {name: field.is_required() for name, field in InteractionSpec.model_fields.items()}

        documented = _interaction_rows()

        assert documented
        assert documented == expected

    def test_documented_ask_outputs_match_interaction_outputs(self) -> None:
        request = InteractionRequest(step_id="ask", mode="text", message="Why?")

        produced = set(Interaction.outputs(request, InteractionAnswer(text="x")))

        assert _documented_output_keys() == produced

    def test_documented_resume_flags_match_the_resume_command(self) -> None:
        documented = _documented_resume_flags()

        assert "--answer" in documented
        assert documented == _resume_command_flags()

    @pytest.mark.parametrize(
        ("heading", "end"),
        [
            pytest.param(_INTERACTION_HEADING, "| Mode |", id="interaction"),
        ],
    )
    def test_a_table_row_missing_from_the_source_is_detected(self, heading: str, end: str) -> None:
        """Fail the field-name comparison when a documented field is absent from the model."""
        section = _section(STEP_SCHEMA_DOC, heading, end) + "| `invented_field` | `string` | No | - | x |\n"

        assert set(_field_rows(section)) != set(InteractionSpec.model_fields)
