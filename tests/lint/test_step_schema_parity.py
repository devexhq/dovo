"""Parity invariant: the `artifacts` Entries table in `docs/reference/step-schema.md` matches `ArtifactPublishSpec`."""

from __future__ import annotations

import re
from typing import Final

from dovo.core.catalog.definitions.step import ArtifactPublishSpec
from tests.lint.astlib import REPO_ROOT

STEP_SCHEMA_DOC: Final = REPO_ROOT / "docs" / "reference" / "step-schema.md"
_HEADING: Final = "## `artifacts` Entries"
_ROW_RE: Final = re.compile(r"^\|\s*`(?P<field>[a-z_]+)`\s*\|[^|]*\|\s*(?P<required>[^|]*?)\s*\|", re.MULTILINE)


def _documented_rows() -> dict[str, bool]:
    """Map each documented `artifacts` entry field to whether the table marks it required."""
    text = STEP_SCHEMA_DOC.read_text(encoding="utf-8")
    section = text.split(_HEADING, 1)[1].split("\n---", 1)[0]
    return {match["field"]: match["required"] == "**Yes**" for match in _ROW_RE.finditer(section)}


class StepSchemaParityTests:
    """The `artifacts` Entries table must agree with `ArtifactPublishSpec` field for field."""

    def test_documented_fields_match_artifact_publish_spec_fields(self) -> None:
        """Documented field names equal the model fields (and the table was actually found)."""
        documented = _documented_rows()

        assert documented
        assert set(documented) == set(ArtifactPublishSpec.model_fields)

    def test_documented_required_flags_match_artifact_publish_spec(self) -> None:
        """Each field's Required cell is `**Yes**` exactly when the model field has no default."""
        expected = {name: field.is_required() for name, field in ArtifactPublishSpec.model_fields.items()}

        assert _documented_rows() == expected
