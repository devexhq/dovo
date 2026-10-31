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
