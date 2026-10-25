"""Diagnostic check validating required host and agent provider binaries on PATH."""

from __future__ import annotations

import shutil
from typing import Final

from dovo.core.agents.registry import PROVIDERS
from dovo.core.config.models import DEFAULT_AGENT
from dovo.core.diagnostics.models import CheckCategory, CheckStatus, DiagnosticCheckResult, DiagnosticsContext

REQUIRED_HOST_BINARIES: Final[tuple[str, ...]] = ("git",)


class EnvBinariesCheck:
    """Diagnostic check validating required host and agent provider CLI binaries on PATH."""

    check_id: str = "env.binaries"
    name: str = "Environment Binaries Check"
    category: CheckCategory = CheckCategory.ENVIRONMENT

    def execute(self, context: DiagnosticsContext) -> DiagnosticCheckResult:
        """Verify `git` and the active agent provider's CLI binary, if any, are present on PATH."""
        provider = context.config.default_agent if context.config is not None else DEFAULT_AGENT
        required = _required_binaries(provider)

        missing = [binary for binary in required if shutil.which(binary) is None]

        if missing:
            return _missing_result(self.check_id, self.name, self.category, missing)

        return _ok_result(self.check_id, self.name, self.category, required)


def _required_binaries(provider: str) -> list[str]:
    """Return the ordered binaries required for the host plus the registry-declared binary of the active provider."""
    required: list[str] = list(REQUIRED_HOST_BINARIES)
    spec = PROVIDERS.get(provider)
    if spec is not None and spec.binary is not None:
        required.append(spec.binary)
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
        error_code="DOCTOR_BINARY_MISSING",
        errors=[],
        warnings=[message],
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
        error_code=None,
        errors=[],
        warnings=[],
    )
