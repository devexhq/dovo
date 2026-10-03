"""Contract tests for agent request DTOs."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dovo.core.agents import AgentAttempt, AgentFailurePayload, AgentRequest, AgentResponseStatus

_PAYLOAD = AgentFailurePayload(
    command="pytest",
    args=["-q"],
    trigger_status="failed",
    exit_code=1,
    timed_out=False,
)


class AgentRequestContractTests:
    @pytest.mark.parametrize(
        ("mode", "with_payload"),
        [
            pytest.param("direct", False, id="direct-no-payload"),
            pytest.param("fix_failure", True, id="fix-failure-payload"),
            pytest.param("review_remediation", True, id="review-remediation-payload"),
        ],
    )
    def test_valid_mode_payload_pairing_constructs_request(self, tmp_path: Path, mode: str, with_payload: bool) -> None:
        """[tier-1/unit] AgentRequest: direct without payload and fix_failure/review_remediation with payload construct, keeping mode, instruction, and payload exactly as given."""
        payload = _PAYLOAD if with_payload else None

        request = AgentRequest.model_validate(
            {
                "mode": mode,
                "instruction": "Do the work.",
                "payload": payload,
                "sandbox_path": tmp_path,
                "timeout_seconds": 5,
            }
        )

        assert request.mode == mode
        assert request.instruction == "Do the work."
        assert request.payload == payload

    @pytest.mark.parametrize(
        ("mode", "instruction", "with_payload", "message"),
        [
            pytest.param("direct", "   ", False, "AgentRequest.instruction must not be blank.", id="blank-instruction"),
            pytest.param(
                "direct",
                "plan",
                True,
                "AgentRequest mode 'direct' must not carry a failure payload.",
                id="direct-with-payload",
            ),
            pytest.param(
                "fix_failure",
                "fix",
                False,
                "AgentRequest mode 'fix_failure' requires a failure payload.",
                id="fix-failure-missing-payload",
            ),
            pytest.param(
                "review_remediation",
                "fix",
                False,
                "AgentRequest mode 'review_remediation' requires a failure payload.",
                id="review-missing-payload",
            ),
        ],
    )
    def test_invalid_pairing_or_blank_instruction_raises_validation_error(
        self, tmp_path: Path, mode: str, instruction: str, with_payload: bool, message: str
    ) -> None:
        """[tier-1/unit] AgentRequest: blank instruction, direct with payload, and remediation modes without payload each raise ValidationError whose text contains the matching literal message."""
        with pytest.raises(ValidationError, match=message.replace(".", r"\.")):
            AgentRequest.model_validate(
                {
                    "mode": mode,
                    "instruction": instruction,
                    "payload": _PAYLOAD if with_payload else None,
                    "sandbox_path": tmp_path,
                    "timeout_seconds": 5,
                }
            )


class AgentAttemptContractTests:
    @pytest.mark.parametrize("status", list(AgentResponseStatus))
    def test_completed_is_true_only_for_proposed_patch_and_no_op(self, status: AgentResponseStatus) -> None:
        """[tier-1/unit] AgentAttempt.completed: True for PROPOSED_PATCH and NO_OP; False for UNFIXABLE, TIMEOUT, and PROVIDER_ERROR."""
        expected = status in {AgentResponseStatus.PROPOSED_PATCH, AgentResponseStatus.NO_OP}

        assert AgentAttempt(status=status).completed is expected
