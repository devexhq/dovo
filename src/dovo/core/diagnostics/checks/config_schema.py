"""Diagnostic check validating `.dovo/config.json` existence and schema conformity."""

from __future__ import annotations

from dovo.common.error_codes import ErrorCode
from dovo.core.config import Config, ConfigLoadResult
from dovo.core.diagnostics.models import CheckCategory, CheckStatus, DiagnosticCheckRecord, DiagnosticsContext


class ConfigSchemaCheck:
    """Diagnostic check validating `.dovo/config.json` existence and schema conformity."""

    check_id: str = "config.schema"
    name: str = "Config Schema Check"
    category: CheckCategory = CheckCategory.CONFIG

    def execute(self, context: DiagnosticsContext) -> DiagnosticCheckRecord:
        """Load `.dovo/config.json` under context.paths and fail with the load's error code when it does not load."""
        loaded = Config(context.paths).load()

        if loaded.ok:
            return _ok_result(self.check_id, self.name, self.category)

        return _failure_result(self.check_id, self.name, self.category, loaded)


def _failure_result(
    check_id: str,
    name: str,
    category: CheckCategory,
    loaded: ConfigLoadResult,
) -> DiagnosticCheckRecord:
    """Build the FAILED result carrying the config load's error code, errors and remediations."""
    details: dict[str, object] = {}
    if loaded.error_code == ErrorCode.CONFIG_SCHEMA_INVALID:
        details["schema_errors"] = loaded.errors
    return DiagnosticCheckRecord(
        check_id=check_id,
        name=name,
        category=category,
        status=CheckStatus.FAILED,
        message=loaded.errors[0],
        details=details,
        error_code=loaded.error_code,
        errors=loaded.errors,
        warnings=[],
        remediations=loaded.remediations,
    )


def _ok_result(check_id: str, name: str, category: CheckCategory) -> DiagnosticCheckRecord:
    """Build the OK result for a valid, schema-conformant configuration file."""
    return DiagnosticCheckRecord(
        check_id=check_id,
        name=name,
        category=category,
        status=CheckStatus.OK,
        message="`.dovo/config.json` is present and passes schema V1 validation.",
        details={},
        error_code=None,
        errors=[],
        warnings=[],
    )
