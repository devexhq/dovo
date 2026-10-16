"""Contract tests for step execution models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dovo.core.agents import AgentResponseStatus
from dovo.engine.executors.models import AgentStepSummary


class AgentStepSummaryTests:
    def test_defaults_and_field_order_serialize_to_exact_json(self) -> None:
        summary = AgentStepSummary(status=AgentResponseStatus.NO_OP)

        assert (
            summary.model_dump_json() == '{"status":"no_op","summary":null,"unfixable_reason":null,"touched_files":[]}'
        )

    def test_unknown_field_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            AgentStepSummary.model_validate({"status": "no_op", "extra": 1})

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            pytest.param(AgentResponseStatus.NO_OP, True, id="no_op"),
            pytest.param(AgentResponseStatus.PROPOSED_PATCH, True, id="proposed_patch"),
            pytest.param(AgentResponseStatus.UNFIXABLE, False, id="unfixable"),
            pytest.param(AgentResponseStatus.TIMEOUT, False, id="timeout"),
            pytest.param(AgentResponseStatus.PROVIDER_ERROR, False, id="provider_error"),
            pytest.param(AgentResponseStatus.BLOCKED, False, id="blocked"),
        ],
    )
    def test_succeeded_is_true_only_for_statuses_with_a_zero_exit_code(
        self, status: AgentResponseStatus, expected: bool
    ) -> None:
        assert AgentStepSummary(status=status).succeeded is expected
