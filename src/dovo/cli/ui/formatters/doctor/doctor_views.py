"""Presentation view models for DiagnosticsReport."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from dovo.common.diagnostic_log import DiagnosticRecord
from dovo.common.models import Remediation
from dovo.core.diagnostics import CheckCategory, CheckStatus


class DoctorCheckView(BaseModel):
    """Semantic view of one diagnostic check outcome: no Rich markup, no composed sentences."""

    model_config = {"extra": "forbid", "strict": True}

    check_id: str
    name: str
    category: CheckCategory
    status: CheckStatus
    message: str
    details: dict[str, Any]
    duration_seconds: float
    error_code: str | None
    errors: list[str]
    warnings: list[str]
    remediations: list[Remediation]
    diagnostics: list[DiagnosticRecord]


class DoctorReportView(BaseModel):
    """Semantic view of an aggregated doctor report: no Rich markup, no composed sentences."""

    model_config = {"extra": "forbid", "strict": True}

    ok: bool
    has_warnings: bool
    workspace_root: Path
    duration_seconds: float
    checks: list[DoctorCheckView]
    diagnostics: list[DiagnosticRecord]
