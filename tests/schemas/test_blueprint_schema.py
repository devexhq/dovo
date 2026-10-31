"""Contract tests for the packaged blueprint JSON schema against the blueprint models."""

from __future__ import annotations

import json
from importlib.resources import files

import pytest

from dovo.core.catalog.definitions import BlueprintDefinition, LoopStepBlock
from dovo.core.catalog.exceptions import BlueprintValidationError


class BlueprintSchemaLoopParityTests:
    """The `loop_step` definition lists exactly the keys a loop block accepts."""

    def test_loop_step_properties_match_loop_block_fields(self) -> None:
        schema = json.loads((files("dovo.schemas.v1") / "blueprint.json").read_text(encoding="utf-8"))
        loop_step = schema["$defs"]["loop_step"]

        assert set(loop_step["properties"]) == set(LoopStepBlock.model_fields)
        assert loop_step["additionalProperties"] is False


class BlueprintSchemaWhenTests:
    """Steps and loop blocks take a non-empty `when` list of condition strings."""

    def test_step_and_loop_when_is_a_non_empty_string_list(self) -> None:
        defs = json.loads((files("dovo.schemas.v1") / "blueprint.json").read_text(encoding="utf-8"))["$defs"]

        assert defs["standard_step"]["properties"]["when"] == {"$ref": "#/$defs/conditions"}
        assert defs["loop_step"]["properties"]["when"] == {"$ref": "#/$defs/conditions"}
        assert defs["conditions"] == {"type": "array", "minItems": 1, "items": {"type": "string"}}


class BlueprintSchemaIfTests:
    """A steps entry may be an `if` block whose branches hold only standard steps."""

    def test_if_step_lists_the_block_keys_and_branches_hold_only_steps(self) -> None:
        defs = json.loads((files("dovo.schemas.v1") / "blueprint.json").read_text(encoding="utf-8"))["$defs"]
        if_step = defs["if_step"]

        assert {"$ref": "#/$defs/if_step"} in defs["step"]["oneOf"]
        assert set(if_step["properties"]) == {"id", "type", "name", "condition", "then", "else"}
        assert if_step["additionalProperties"] is False
        assert if_step["properties"]["then"]["items"] == {"$ref": "#/$defs/standard_step"}
        assert if_step["properties"]["else"]["items"] == {"$ref": "#/$defs/standard_step"}


_BLOCK_ENTRIES: dict[str, dict[str, object]] = {
    "loop_step": {
        "id": "fix",
        "type": "loop",
        "until": ["steps.edit.exit_code == 0"],
        "do": [{"id": "edit", "run": "true"}],
    },
    "if_step": {
        "id": "route",
        "type": "if",
        "condition": ["steps.check.exit_code == 0"],
        "then": [{"id": "ship", "run": "true"}],
    },
}


def _loads_without(entry: dict[str, object], key: str) -> bool:
    """Return whether a blueprint holding a `check` step and entry without key passes the blueprint model."""
    trimmed = {name: value for name, value in entry.items() if name != key}
    try:
        BlueprintDefinition.from_document({"steps": [{"id": "check", "run": "true"}, trimmed]}, key="bp")
    except BlueprintValidationError:
        return False
    return True


class BlueprintSchemaRequiredParityTests:
    """A block definition requires exactly the keys whose omission the blueprint loader rejects."""

    @pytest.mark.parametrize("definition", sorted(_BLOCK_ENTRIES))
    def test_required_keys_match_the_keys_the_loader_rejects_without(self, definition: str) -> None:
        defs = json.loads((files("dovo.schemas.v1") / "blueprint.json").read_text(encoding="utf-8"))["$defs"]
        entry = _BLOCK_ENTRIES[definition]

        rejected_without = {key for key in entry if not _loads_without(entry, key)}

        assert set(defs[definition]["required"]) == rejected_without

    def test_standard_step_id_is_optional_because_the_loader_derives_it(self) -> None:
        defs = json.loads((files("dovo.schemas.v1") / "blueprint.json").read_text(encoding="utf-8"))["$defs"]

        assert _loads_without({"id": "ship", "run": "true"}, "id")
        assert "id" not in defs["standard_step"].get("required", [])
