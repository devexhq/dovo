"""Parity invariant: `schemas/v1/config.json` and the `DovoConfig` Pydantic models describe the same keys."""

from __future__ import annotations

import json
from typing import Any, Final, get_args

import pytest
from pydantic import BaseModel

from dovo.core.config.models import AgentProvider, DovoConfig
from tests.lint.astlib import SRC_ROOT

SCHEMA_PATH: Final = SRC_ROOT / "schemas" / "v1" / "config.json"


def _load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _section_models() -> list[tuple[str, type[BaseModel]]]:
    """Pair each `DovoConfig` field that is itself a model with its section name."""
    sections: list[tuple[str, type[BaseModel]]] = []
    for name, field in DovoConfig.model_fields.items():
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            sections.append((name, annotation))
    return sections


class ConfigSchemaParityTests:
    """The packaged config schema must agree with the config models key for key."""

    def test_top_level_schema_keys_match_dovo_config_fields(self) -> None:
        """Top-level schema properties equal the `DovoConfig` fields."""
        schema = _load_schema()

        assert set(schema["properties"]) == set(DovoConfig.model_fields)

    def test_top_level_required_keys_match_required_dovo_config_fields(self) -> None:
        """Schema `required` equals the `DovoConfig` fields without defaults."""
        schema = _load_schema()
        required_fields = {name for name, field in DovoConfig.model_fields.items() if field.is_required()}

        assert set(schema["required"]) == required_fields

    @pytest.mark.parametrize(
        ("section", "model"),
        [pytest.param(name, model, id=name) for name, model in _section_models()],
    )
    def test_section_schema_keys_match_section_model_fields(self, section: str, model: type[BaseModel]) -> None:
        """Each config section's schema properties equal its model fields."""
        schema = _load_schema()

        assert set(schema["properties"][section]["properties"]) == set(model.model_fields)

    def test_agent_provider_enum_matches_agent_provider_literal(self) -> None:
        """The schema's provider enum equals the `AgentProvider` literal."""
        schema = _load_schema()

        assert set(schema["properties"]["agent"]["properties"]["provider"]["enum"]) == set(get_args(AgentProvider))
