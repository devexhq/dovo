"""Contract tests for step execution models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dovo.core.agents import AgentResponseStatus
from dovo.engine.executors.models import AgentStepSummary


class AgentStepSummaryTests:
    """[tier-1/unit] AgentStepSummary: stdout wire shape of an agent step."""

    def test_defaults_and_field_order_serialize_to_exact_json(self) -> None:
        """[tier-1/unit] AgentStepSummary: only status given serializes to the exact compact JSON with null summary and unfixable_reason and an empty touched_files, in declared field order."""
        summary = AgentStepSummary(status=AgentResponseStatus.NO_OP)

        assert (
            summary.model_dump_json() == '{"status":"no_op","summary":null,"unfixable_reason":null,"touched_files":[]}'
        )

    def test_unknown_field_raises_validation_error(self) -> None:
        """[tier-1/unit] AgentStepSummary: an unknown field raises a pydantic ValidationError."""
        with pytest.raises(ValidationError):
            AgentStepSummary.model_validate({"status": "no_op", "extra": 1})
