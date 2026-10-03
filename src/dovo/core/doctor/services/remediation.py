"""Deterministic remediation resolution for failing and warning diagnostic checks."""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from dovo.core.doctor.models import CheckStatus, DiagnosticCheckResult, Remediation, RemediationType


def resolve_remediations(result: DiagnosticCheckResult) -> list[Remediation]:
    """Return deterministic remediation actions for a failed or warning diagnostic check."""
    if result.status not in (CheckStatus.FAILED, CheckStatus.WARNING):
        return []
    builder = _ERROR_CODE_BUILDERS.get(result.error_code or "")
    if builder is None:
        return [_fallback_remediation(result)]
    return builder(result)


def format_remediation_summary(remediations: list[Remediation]) -> str:
    """Format a list of remediations into user-facing text hints."""
    if not remediations:
        return ""
    lines = ["Fix:"]
    for remediation in remediations:
        lines.append(f"  • {remediation.title}:")
        if remediation.action_type == RemediationType.COMMAND and remediation.command:
            lines.append(f"    {remediation.command}")
        else:
            lines.append(f"    {remediation.description}")
    return "\n".join(lines)


def _fallback_remediation(result: DiagnosticCheckResult) -> Remediation:
    """Return the generic manual-inspection remediation for a check with no mapped error code."""
    code = result.error_code or "DOCTOR_UNKNOWN"
    return Remediation(
        code=code,
        title="Investigate check failure manually",
        action_type=RemediationType.MANUAL,
        command=None,
        description=(
            f"No deterministic remediation is registered for check '{result.check_id}' "
            f"(error_code='{code}'). Review the check message and details to diagnose and resolve the issue."
        ),
        doc_path=None,
        is_automated=False,
    )


def _git_not_repo(result: DiagnosticCheckResult) -> list[Remediation]:
    """Return the remediation for a workspace that is not a Git repository (DOCTOR_GIT_NOT_REPO)."""
    return [
        Remediation(
            code="DOCTOR_GIT_NOT_REPO",
            title="Initialize Git repository",
            action_type=RemediationType.COMMAND,
            command="git init",
            description="Run `git init` from the workspace root to create the Git repository worktree operations require.",
            doc_path=None,
            is_automated=False,
        )
    ]


def _git_binary_missing(result: DiagnosticCheckResult) -> list[Remediation]:
    """Return the remediation for a missing git executable on PATH (DOCTOR_GIT_BINARY_MISSING)."""
    return [
        Remediation(
            code="DOCTOR_GIT_BINARY_MISSING",
            title="Install Git",
            action_type=RemediationType.MANUAL,
            command=None,
            description="Install Git via your operating system's package manager and ensure the `git` executable is available on PATH.",
            doc_path=None,
            is_automated=False,
        )
    ]


def _config_not_found(result: DiagnosticCheckResult) -> list[Remediation]:
    """Return the remediation for a missing `.dovo/config.json` (DOCTOR_CONFIG_NOT_FOUND)."""
    return [
        Remediation(
            code="DOCTOR_CONFIG_NOT_FOUND",
            title="Initialize Dovo workspace",
            action_type=RemediationType.COMMAND,
            command="dovo init",
            description="Run `dovo init` to create `.dovo/config.json` and the required workspace directory structure.",
            doc_path="docs/cli/init.md",
            is_automated=False,
        )
    ]


def _config_malformed(result: DiagnosticCheckResult) -> list[Remediation]:
    """Return the remediation for a `.dovo/config.json` with invalid JSON syntax (DOCTOR_CONFIG_MALFORMED)."""
    return [
        Remediation(
            code="DOCTOR_CONFIG_MALFORMED",
            title="Repair configuration file",
            action_type=RemediationType.COMMAND,
            command="dovo init --repair",
            description="Run `dovo init --repair` to restore missing schema keys, or edit `.dovo/config.json` by hand to fix the invalid JSON syntax.",
            doc_path="docs/cli/init.md",
            is_automated=False,
        )
    ]


def _config_schema_invalid(result: DiagnosticCheckResult) -> list[Remediation]:
    """Return the remediation for a `.dovo/config.json` failing schema V1 validation (DOCTOR_CONFIG_SCHEMA_INVALID)."""
    return [
        Remediation(
            code="DOCTOR_CONFIG_SCHEMA_INVALID",
            title="Validate and repair configuration",
            action_type=RemediationType.COMMAND,
            command="dovo config validate",
            description="Run `dovo config validate` to see detailed schema errors, then fix `.dovo/config.json` or run `dovo init --repair` to restore missing defaults.",
            doc_path="docs/cli/config.md",
            is_automated=False,
        )
    ]


def _fs_unwritable(result: DiagnosticCheckResult) -> list[Remediation]:
    """Return one remediation per unwritable workspace path, or a generic fallback when none are named (DOCTOR_FS_UNWRITABLE)."""
    unwritable_paths = result.details.get("unwritable_paths")
    paths = unwritable_paths if isinstance(unwritable_paths, list) and unwritable_paths else [".dovo"]
    return [
        Remediation(
            code="DOCTOR_FS_UNWRITABLE",
            title="Fix directory permissions",
            action_type=RemediationType.COMMAND,
            command=f"chmod u+w {path}",
            description=f"Grant the current user write access to '{path}' so Dovo can create and update workspace state.",
            doc_path=None,
            is_automated=False,
        )
        for path in paths
    ]


def _sandbox_stale(result: DiagnosticCheckResult) -> list[Remediation]:
    """Return the remediation for stale sandbox database references (DOCTOR_SANDBOX_STALE)."""
    return [
        Remediation(
            code="DOCTOR_SANDBOX_STALE",
            title="Prune stale sandboxes",
            action_type=RemediationType.COMMAND,
            command="dovo sandbox prune",
            description="Run `dovo sandbox prune` to reconcile sandbox database records that no longer match a live Git worktree.",
            doc_path="docs/cli/sandbox.md",
            is_automated=False,
        )
    ]


def _sandbox_orphan(result: DiagnosticCheckResult) -> list[Remediation]:
    """Return the remediation for orphaned sandbox worktree directories (DOCTOR_SANDBOX_ORPHAN)."""
    return [
        Remediation(
            code="DOCTOR_SANDBOX_ORPHAN",
            title="Prune orphan worktree directories",
            action_type=RemediationType.COMMAND,
            command="dovo sandbox prune",
            description="Run `dovo sandbox prune` to remove sandbox worktree directories that have no matching database record.",
            doc_path="docs/cli/sandbox.md",
            is_automated=False,
        )
    ]


def _binary_missing(result: DiagnosticCheckResult) -> list[Remediation]:
    """Return one remediation per missing required binary, or a generic fallback when none are named (DOCTOR_BINARY_MISSING)."""
    missing_binaries = result.details.get("missing_binaries")
    binaries = missing_binaries if isinstance(missing_binaries, list) and missing_binaries else ["<binary>"]
    return [
        Remediation(
            code="DOCTOR_BINARY_MISSING",
            title="Install required binary",
            action_type=RemediationType.MANUAL,
            command=None,
            description=f"Install '{binary}' via your package manager and ensure it is available on PATH.",
            doc_path=None,
            is_automated=False,
        )
        for binary in binaries
    ]


def _agent_key_missing(result: DiagnosticCheckResult) -> list[Remediation]:
    """Return the remediation naming the missing agent provider credential environment variable (DOCTOR_AGENT_KEY_MISSING)."""
    missing_env_var = result.details.get("missing_env_var")
    env_var = missing_env_var if isinstance(missing_env_var, str) and missing_env_var else "<PROVIDER>_API_KEY"
    return [
        Remediation(
            code="DOCTOR_AGENT_KEY_MISSING",
            title="Export agent API key",
            action_type=RemediationType.COMMAND,
            command=f'export {env_var}="<your-api-key>"',
            description=f"Set the {env_var} environment variable in your active shell or `.env` file so the configured agent provider can authenticate.",
            doc_path="docs/guides/agent-providers.md",
            is_automated=False,
        )
    ]


def _agent_no_model(result: DiagnosticCheckResult) -> list[Remediation]:
    """Return the remediation for an agent provider with no model configured (DOCTOR_AGENT_NO_MODEL)."""
    return [
        Remediation(
            code="DOCTOR_AGENT_NO_MODEL",
            title="Configure agent model",
            action_type=RemediationType.COMMAND,
            command='dovo config set agent.model "<model>"',
            description="Set `agent.model` in `.dovo/config.json` to a model supported by the configured provider, e.g. `dovo config set agent.model <model-name>`.",
            doc_path="docs/cli/config.md",
            is_automated=False,
        )
    ]


_ERROR_CODE_BUILDERS: Final[dict[str, Callable[[DiagnosticCheckResult], list[Remediation]]]] = {
    "DOCTOR_GIT_NOT_REPO": _git_not_repo,
    "DOCTOR_GIT_BINARY_MISSING": _git_binary_missing,
    "DOCTOR_CONFIG_NOT_FOUND": _config_not_found,
    "DOCTOR_CONFIG_MALFORMED": _config_malformed,
    "DOCTOR_CONFIG_SCHEMA_INVALID": _config_schema_invalid,
    "DOCTOR_FS_UNWRITABLE": _fs_unwritable,
    "DOCTOR_SANDBOX_STALE": _sandbox_stale,
    "DOCTOR_SANDBOX_ORPHAN": _sandbox_orphan,
    "DOCTOR_BINARY_MISSING": _binary_missing,
    "DOCTOR_AGENT_KEY_MISSING": _agent_key_missing,
    "DOCTOR_AGENT_NO_MODEL": _agent_no_model,
}
