"""Tests for the shared JSON schema validator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dovo.common.schema_validation import SchemaValidator

ITEMS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": {"type": "string", "pattern": "^[A-Z]+$"}},
        "worktree": {"type": "object", "properties": {"max_active_worktrees": {"type": "integer"}}},
    },
}


def _write_schema(tmp_path: Path) -> Path:
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps(ITEMS_SCHEMA), encoding="utf-8")
    return schema_path


class SchemaValidatorTests:
    def test_validate_renders_list_index_in_brackets(self, tmp_path: Path) -> None:
        """[tier-1/unit] SchemaValidator.validate: document {"items": ["OK", 5]} returns errors == ["items[1]: 5 is not of type 'string'"]."""
        validator = SchemaValidator(_write_schema(tmp_path))

        result = validator.validate({"items": ["OK", 5]})

        assert result.errors == ["items[1]: 5 is not of type 'string'"]

    def test_validate_keeps_dotted_path_for_object_keys(self, tmp_path: Path) -> None:
        """[tier-1/unit] SchemaValidator.validate: nested object key failure still renders "worktree.max_active_worktrees: 'five' is not of type 'integer'"."""
        validator = SchemaValidator(_write_schema(tmp_path))

        result = validator.validate({"worktree": {"max_active_worktrees": "five"}})

        assert result.errors == ["worktree.max_active_worktrees: 'five' is not of type 'integer'"]

    def test_validate_echoes_pattern_instance_by_default(self, tmp_path: Path) -> None:
        """[tier-1/unit] SchemaValidator.validate: default validator on instance "TOKEN=abc" failing a pattern returns an error containing "'TOKEN=abc' does not match"."""
        validator = SchemaValidator(_write_schema(tmp_path))

        result = validator.validate({"items": ["TOKEN=abc"]})

        assert "'TOKEN=abc' does not match" in result.errors[0]

    def test_validate_hides_pattern_instance_when_flag_set(self, tmp_path: Path) -> None:
        """[tier-1/unit] SchemaValidator.validate: hide_pattern_instances=True on instance "TOKEN=abc" failing a pattern returns errors == ["items[0]: does not match pattern '^[A-Z]+$'"]."""
        validator = SchemaValidator(_write_schema(tmp_path), hide_pattern_instances=True)

        result = validator.validate({"items": ["TOKEN=abc"]})

        assert result.errors == ["items[0]: does not match pattern '^[A-Z]+$'"]
