"""Diagnostic check validating `.dovo/config.json` existence and schema conformity."""

from __future__ import annotations

from dovo.core.config.loader import ConfigLoadResult, ConfigLoadStatus, load_config_at
from dovo.core.doctor.models import CheckCategory, CheckStatus, DiagnosticCheckResult, DoctorContext


class ConfigSchemaCheck:
    """Diagnostic check validating `.dovo/config.json` existence and schema conformity."""

    check_id: str = "config.schema"
    name: str = "Config Schema Check"
    category: CheckCategory = CheckCategory.CONFIG

    def execute(self, context: DoctorContext) -> DiagnosticCheckResult:
        """Load and validate `.dovo/config.json` under context.paths against config schema V1."""
        loaded = load_config_at(context.paths.config_file)

        if loaded.status == ConfigLoadStatus.OK:
            return _ok_result(self.check_id, self.name, self.category)

        return _failure_result(self.check_id, self.name, self.category, loaded)


_STATUS_TO_ERROR_CODE: dict[ConfigLoadStatus, str] = {
    ConfigLoadStatus.NOT_FOUND: "DOCTOR_CONFIG_NOT_FOUND",
    ConfigLoadStatus.MALFORMED_JSON: "DOCTOR_CONFIG_MALFORMED",
    ConfigLoadStatus.ROOT_NOT_OBJECT: "DOCTOR_CONFIG_SCHEMA_INVALID",
    ConfigLoadStatus.SCHEMA_INVALID: "DOCTOR_CONFIG_SCHEMA_INVALID",
    ConfigLoadStatus.PATH_IS_DIRECTORY: "DOCTOR_CONFIG_SCHEMA_INVALID",
    ConfigLoadStatus.UNREADABLE: "DOCTOR_CONFIG_SCHEMA_INVALID",
}


def _failure_result(
    check_id: str,
    name: str,
    category: CheckCategory,
    loaded: ConfigLoadResult,
) -> DiagnosticCheckResult:
    """Build the FAILED result carrying the mapped error code and loader diagnostics."""
    error_code = _STATUS_TO_ERROR_CODE[loaded.status]
    details: dict[str, object] = {}
    if error_code == "DOCTOR_CONFIG_SCHEMA_INVALID":
        details["schema_errors"] = loaded.errors
    message = loaded.errors[0] if loaded.errors else f"Config validation failed with status '{loaded.status}'."
    return DiagnosticCheckResult(
        check_id=check_id,
        name=name,
        category=category,
        status=CheckStatus.FAILED,
        message=message,
        details=details,
        duration_ms=0.0,
        error_code=error_code,
        errors=loaded.errors,
        warnings=[],
        fixes=[],
    )


def _ok_result(check_id: str, name: str, category: CheckCategory) -> DiagnosticCheckResult:
    """Build the OK result for a valid, schema-conformant configuration file."""
    return DiagnosticCheckResult(
        check_id=check_id,
        name=name,
        category=category,
        status=CheckStatus.OK,
        message="`.dovo/config.json` is present and passes schema V1 validation.",
        details={},
        duration_ms=0.0,
        error_code=None,
        errors=[],
        warnings=[],
        fixes=[],
    )
