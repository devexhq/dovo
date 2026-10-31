"""Contract tests for the packaged blueprint JSON schema against the blueprint models."""

from __future__ import annotations

import json
from importlib.resources import files

from dovo.core.catalog.definitions import LoopStepBlock


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
