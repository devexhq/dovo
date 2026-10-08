"""Contract tests for agent request DTOs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from dovo.core.agents import (
    AgentAttempt,
    AgentFailurePayload,
    AgentInvocationContext,
    AgentRequest,
    AgentResponseStatus,
    AgentScratchResult,
    CliMutationRunRequest,
)

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
                "worktree_path": tmp_path,
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
                    "worktree_path": tmp_path,
                    "timeout_seconds": 5,
                }
            )


class AgentAttemptContractTests:
    @pytest.mark.parametrize("status", list(AgentResponseStatus))
    def test_completed_is_true_only_for_proposed_patch_and_no_op(self, status: AgentResponseStatus) -> None:
        """[tier-1/unit] AgentAttempt.completed: True for PROPOSED_PATCH and NO_OP; False for UNFIXABLE, TIMEOUT, and PROVIDER_ERROR."""
        expected = status in {AgentResponseStatus.PROPOSED_PATCH, AgentResponseStatus.NO_OP}

        assert AgentAttempt(status=status).completed is expected


def _context(root: Path) -> AgentInvocationContext:
    return AgentInvocationContext(invocation_id="a" * 32, scratch_path=root / "scratch", control_path=root / "control")


class InvocationContextModelTests:
    @pytest.mark.parametrize(
        "overrides",
        [
            pytest.param({"invocation_id": "abc"}, id="short-id"),
            pytest.param({"invocation_id": "A" * 32}, id="uppercase-id"),
            pytest.param({"scratch_path": "scratch"}, id="str-path"),
            pytest.param({"extra": 1}, id="extra-field"),
        ],
    )
    def test_invalid_context_fields_raise_validation_error(self, tmp_path: Path, overrides: dict[str, object]) -> None:
        """[tier-1/unit] AgentInvocationContext: a short id, an uppercase id, a str path, or an extra field raises ValidationError."""
        fields: dict[str, object] = {
            "invocation_id": "a" * 32,
            "scratch_path": tmp_path / "scratch",
            "control_path": tmp_path / "control",
        }

        with pytest.raises(ValidationError):
            AgentInvocationContext.model_validate({**fields, **overrides})

    def test_context_is_frozen(self, tmp_path: Path) -> None:
        """[tier-1/unit] AgentInvocationContext: assigning invocation_id on a constructed context raises ValidationError."""
        context = _context(tmp_path)

        with pytest.raises(ValidationError):
            context.invocation_id = "b" * 32  # pyright: ignore[reportAttributeAccessIssue]  # intentional ill-typed test input: frozen-model write

    @pytest.mark.parametrize(
        "request_kind", [pytest.param("agent", id="agent-request"), pytest.param("run", id="run-request")]
    )
    def test_invocation_is_excluded_from_both_request_dumps(self, tmp_path: Path, request_kind: str) -> None:
        """[tier-1/unit] AgentRequest / CliMutationRunRequest: with invocation set, model_dump() and model_dump_json() contain no 'invocation' key while request.invocation is still the context."""
        context = _context(tmp_path)
        request: AgentRequest | CliMutationRunRequest
        if request_kind == "agent":
            request = AgentRequest(
                mode="direct", instruction="go", worktree_path=tmp_path, timeout_seconds=5, invocation=context
            )
        else:
            request = CliMutationRunRequest(
                worktree_path=tmp_path, prompt="go", timeout_seconds=5.0, invocation=context
            )

        assert "invocation" not in request.model_dump()
        assert "invocation" not in json.loads(request.model_dump_json())
        assert request.invocation == context

    def test_agent_scratch_path_must_match_the_invocation_scratch_path(self, tmp_path: Path) -> None:
        """[tier-1/unit] AgentRequest: agent_scratch_path equal to invocation.scratch_path or None constructs; a different path raises ValidationError."""
        context = _context(tmp_path)
        base = {"mode": "direct", "instruction": "go", "worktree_path": tmp_path, "timeout_seconds": 5}

        matching = AgentRequest.model_validate(
            {**base, "invocation": context, "agent_scratch_path": context.scratch_path}
        )
        unset = AgentRequest.model_validate({**base, "invocation": context})

        assert matching.agent_scratch_path == context.scratch_path
        assert unset.agent_scratch_path is None
        with pytest.raises(ValidationError):
            AgentRequest.model_validate({**base, "invocation": context, "agent_scratch_path": tmp_path / "other"})


class AgentScratchResultTests:
    def test_ok_requires_a_context(self, tmp_path: Path) -> None:
        """[tier-1/unit] AgentScratchResult: ok is True only when a context is present; a failure result keeps its invocation_id."""
        success = AgentScratchResult(invocation_id="a" * 32, context=_context(tmp_path))
        failure = AgentScratchResult(invocation_id="a" * 32, errors=["x"], error_code="AGENT_SCRATCH_UNAVAILABLE")

        assert success.ok
        assert not failure.ok
        assert failure.invocation_id == "a" * 32
