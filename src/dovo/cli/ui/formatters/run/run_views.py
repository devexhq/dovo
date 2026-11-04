"""Semantic view models for run formatters."""

from __future__ import annotations

from pydantic import BaseModel, Field

from dovo.common.diagnostic_log import DiagnosticRecord
from dovo.common.models import Remediation
from dovo.core.db import SessionStatus


class RunResultView(BaseModel):
    """Semantic view of a run or resume result: the shared header first, then the session it ran."""

    model_config = {"extra": "forbid", "strict": True}

    ok: bool
    error_code: str | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    remediations: list[Remediation] = Field(default_factory=list)
    diagnostics: list[DiagnosticRecord] = Field(default_factory=list)
    session_id: str | None = None
    blueprint_name: str | None = None
    session_status: SessionStatus | None = None
    paused_prompt_id: str | None = None
