"""Diagnostic check validating required host and agent provider binaries on PATH."""

from __future__ import annotations

import shutil
from typing import Final

from dovo.core.config.models import AgentConfig, AgentProvider
from dovo.core.doctor.models import CheckCategory, CheckStatus, DiagnosticCheckResult, DoctorContext

REQUIRED_HOST_BINARIES: Final[tuple[str, ...]] = ("git",)

PROVIDER_REQUIRED_BINARY: Final[dict[AgentProvider, str]] = {
    "gemini": "gemini",
    "copilot": "gh",
}


class EnvBinariesCheck:
    """Diagnostic check validating required host and agent provider CLI binaries on PATH."""

    check_id: str = "env.binaries"
    name: str = "Environment Binaries Check"
    category: CheckCategory = CheckCategory.ENVIRONMENT

    def execute(self, context: DoctorContext) -> DiagnosticCheckResult:
        """Verify `git` and the active agent provider's CLI binary, if any, are present on PATH."""
        agent_config = context.config.agent if context.config is not None else AgentConfig()
        required = _required_binaries(agent_config)

        missing = [binary for binary in required if shutil.which(binary) is None]

        if missing:
            return _missing_result(self.check_id, self.name, self.category, missing)

        return _ok_result(self.check_id, self.name, self.category, required)


def _required_binaries(agent_config: AgentConfig) -> list[str]:
    """Return the ordered list of binaries required for the host and the active provider."""
    required: list[str] = list(REQUIRED_HOST_BINARIES)
    provider_binary = PROVIDER_REQUIRED_BINARY.get(agent_config.provider)
    if provider_binary is not None:
        required.append(provider_binary)
    return required


def _missing_result(check_id: str, name: str, category: CheckCategory, missing: list[str]) -> DiagnosticCheckResult:
    """Build the WARNING DOCTOR_BINARY_MISSING result naming the binaries absent from PATH."""
    message = f"{len(missing)} required binary(s) not found on PATH: {', '.join(missing)}."
    return DiagnosticCheckResult(
        check_id=check_id,
        name=name,
        category=category,
        status=CheckStatus.WARNING,
        message=message,
        details={"missing_binaries": missing},
        duration_ms=0.0,
        error_code="DOCTOR_BINARY_MISSING",
        errors=[],
        warnings=[message],
        fixes=[],
    )


def _ok_result(check_id: str, name: str, category: CheckCategory, verified: list[str]) -> DiagnosticCheckResult:
    """Build the OK result listing every verified binary."""
    return DiagnosticCheckResult(
        check_id=check_id,
        name=name,
        category=category,
        status=CheckStatus.OK,
        message=f"{len(verified)} required binary(s) verified on PATH.",
        details={"verified_binaries": verified},
        duration_ms=0.0,
        error_code=None,
        errors=[],
        warnings=[],
        fixes=[],
    )
