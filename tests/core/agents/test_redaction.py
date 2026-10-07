"""Contract tests for invocation-time masking of agent responses."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.redact import SecretRedactor
from dovo.core.agents import AgentResponse, AgentResponseStatus
from dovo.core.agents.redaction import build_response_redactor, redact_agent_response


class AgentResponseRedactionTests:
    def test_redact_agent_response_masks_diagnostic_fields_and_preserves_the_rest(self) -> None:
        """[tier-1/unit] redact_agent_response: errors ["a s3cr3t-value","b"], raw_text, and summary containing "s3cr3t-value" are masked in order, while status, duration_ms, mutation_baseline_ref, fixes, unfixable_reason, and a unified_diff containing "s3cr3t-value" are equal to the input."""
        redactor = SecretRedactor([("SVC_TOKEN", "s3cr3t-value")])
        response = AgentResponse(
            status=AgentResponseStatus.PROPOSED_PATCH,
            errors=["a s3cr3t-value", "b"],
            raw_text="raw s3cr3t-value",
            summary="sum s3cr3t-value",
            unified_diff="+s3cr3t-value\n",
            unfixable_reason="why s3cr3t-value",
            fixes=["fix s3cr3t-value"],
            duration_ms=42,
            mutation_baseline_ref="abc123",
        )

        masked = redact_agent_response(response, redactor)

        assert masked.errors == ["a [REDACTED:SVC_TOKEN]", "b"]
        assert masked.raw_text == "raw [REDACTED:SVC_TOKEN]"
        assert masked.summary == "sum [REDACTED:SVC_TOKEN]"
        assert masked.status == AgentResponseStatus.PROPOSED_PATCH
        assert masked.duration_ms == 42
        assert masked.mutation_baseline_ref == "abc123"
        assert masked.fixes == ["fix s3cr3t-value"]
        assert masked.unfixable_reason == "why s3cr3t-value"
        assert masked.unified_diff == "+s3cr3t-value\n"

    def test_redact_agent_response_preserves_none_fields(self) -> None:
        """[tier-1/unit] redact_agent_response: raw_text=None and summary=None stay None and errors=[] stays []."""
        redactor = SecretRedactor([("SVC_TOKEN", "s3cr3t-value")])
        response = AgentResponse(status=AgentResponseStatus.NO_OP)

        masked = redact_agent_response(response, redactor)

        assert (masked.raw_text, masked.summary, masked.errors) == (None, None, [])


class ResponseRedactorTests:
    def test_build_response_redactor_masks_all_alternatives_below_the_floor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] build_response_redactor: credential_envs ("GH_TOKEN","GITHUB_TOKEN") with values "abc" and " xyz " mask "abc" and "xyz" as [REDACTED:GH_TOKEN] and [REDACTED:GITHUB_TOKEN]."""
        monkeypatch.setenv("GH_TOKEN", "abc")
        monkeypatch.setenv("GITHUB_TOKEN", " xyz ")

        redactor = build_response_redactor(("GH_TOKEN", "GITHUB_TOKEN"), tmp_path)

        assert redactor.redact_text("abc xyz") == "[REDACTED:GH_TOKEN] [REDACTED:GITHUB_TOKEN]"

    def test_build_response_redactor_masks_worktree_env_file_values(self, tmp_path: Path) -> None:
        """[tier-1/unit] build_response_redactor: tmp_path/.env with DB_PASSWORD=hunter2-long masks that value as [REDACTED:DB_PASSWORD]."""
        (tmp_path / ".env").write_text("DB_PASSWORD=hunter2-long\n", encoding="utf-8")

        redactor = build_response_redactor((), tmp_path)

        assert redactor.redact_text("hunter2-long") == "[REDACTED:DB_PASSWORD]"
