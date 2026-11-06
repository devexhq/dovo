"""Deterministic remediation resolution for failing and warning diagnostic checks."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

from dovo.common.error_codes import ErrorCode
from dovo.common.models import Remediation
from dovo.core.diagnostics.models import CheckStatus, DiagnosticCheckRecord


def resolve_remediations(result: DiagnosticCheckRecord) -> list[Remediation]:
    """Return a failed or warning check's own remediations, or the deterministic ones for its error code."""
    if result.status not in (CheckStatus.FAILED, CheckStatus.WARNING):
        return []
    if result.remediations:
        return list(result.remediations)
    builder = _ERROR_CODE_BUILDERS.get(result.error_code) if result.error_code else None
    if builder is None:
        return [_fallback_remediation(result)]
    return builder(result)


def _fallback_remediation(result: DiagnosticCheckRecord) -> Remediation:
    """Return the generic manual-inspection remediation for a check with no mapped error code."""
    code = result.error_code or "none"
    return Remediation(
        message=(
            f"No deterministic remediation is registered for check '{result.check_id}' "
            f"(error_code='{code}'). Review the check message and details to diagnose and resolve the issue."
        ),
    )


def _git_repo_not_found(result: DiagnosticCheckRecord) -> list[Remediation]:
    """Return the remediation for a workspace that is not a Git repository (DOCTOR_GIT_REPO_NOT_FOUND)."""
    return [
        Remediation(
            message="Run 'git init' from the workspace root to create the Git repository worktree operations require.",
            command="git init",
        )
    ]


def _git_binary_missing(result: DiagnosticCheckRecord) -> list[Remediation]:
    """Return the remediation for a missing git executable on PATH (DOCTOR_GIT_BINARY_MISSING)."""
    return [
        Remediation(
            message=(
                "Install Git via your operating system's package manager and ensure the 'git' executable "
                "is available on 'PATH'."
            ),
        )
    ]


def _git_timeout(result: DiagnosticCheckRecord) -> list[Remediation]:
    """Return the remediation for a git command that timed out (DOCTOR_GIT_TIMEOUT)."""
    return [
        Remediation(
            message=(
                "Check for git lock files, credential prompts, or stuck git processes, then retry, "
                "or raise 'DOVO_GIT_TIMEOUT_SECONDS' for a slow repository."
            ),
        )
    ]


def _fs_unwritable(result: DiagnosticCheckRecord) -> list[Remediation]:
    """Return one remediation per unwritable workspace path, or a generic fallback when none are named (DOCTOR_FS_UNWRITABLE)."""
    unwritable_paths = result.details.get("unwritable_paths")
    paths = unwritable_paths if isinstance(unwritable_paths, list) and unwritable_paths else [".dovo"]
    return [
        Remediation(
            message=(
                f"Run 'chmod u+w {path}' to grant the current user write access to '{path}' "
                "so Dovo can create and update workspace state."
            ),
            command=f"chmod u+w {path}",
        )
        for path in paths
    ]


def _worktree_stale(result: DiagnosticCheckRecord) -> list[Remediation]:
    """Return the remediation for stale worktree database references (DOCTOR_WORKTREE_STALE)."""
    return [
        Remediation(
            message="Run 'dovo worktree prune' to reconcile worktree database records that no longer match a live Git worktree.",
            command="dovo worktree prune",
        )
    ]


def _worktree_orphaned(result: DiagnosticCheckRecord) -> list[Remediation]:
    """Return the remediation for orphaned worktree directories (DOCTOR_WORKTREE_ORPHANED)."""
    return [
        Remediation(
            message="Run 'dovo worktree prune' to remove worktree directories that have no matching database record.",
            command="dovo worktree prune",
        )
    ]


def _binary_missing(result: DiagnosticCheckRecord) -> list[Remediation]:
    """Return one remediation per missing required binary, or a generic fallback when none are named (DOCTOR_BINARY_MISSING)."""
    missing_binaries = result.details.get("missing_binaries")
    binaries = missing_binaries if isinstance(missing_binaries, list) and missing_binaries else ["<binary>"]
    return [
        Remediation(message=f"Install '{binary}' via your package manager and ensure it is available on 'PATH'.")
        for binary in binaries
    ]


def _credential_names(details: dict[str, Any]) -> list[str]:
    """Return the missing credential names from details, falling back to missing_env_var, then to a placeholder."""
    credential_envs = details.get("credential_envs")
    if (
        isinstance(credential_envs, list)
        and credential_envs
        and all(isinstance(name, str) and name for name in credential_envs)
    ):
        return list(credential_envs)

    missing_env_var = details.get("missing_env_var")

    return [missing_env_var if isinstance(missing_env_var, str) and missing_env_var else "<PROVIDER>_API_KEY"]


def _claude_login_remediation() -> Remediation:
    """Return the manual remediation for authenticating Claude Code through 'claude auth login'."""
    return Remediation(
        message=(
            "Run 'claude auth login', or export 'CLAUDE_CODE_OAUTH_TOKEN' from 'claude setup-token', "
            "so Claude Code authenticates without an API key."
        ),
    )


def _agent_key_missing(result: DiagnosticCheckRecord) -> list[Remediation]:
    """Return one export remediation per missing credential name, plus the Claude login remediation for claude (DOCTOR_AGENT_KEY_MISSING)."""
    remediations = [
        Remediation(
            message=(
                f"Run 'export {env_var}=\"<your-api-key>\"' in your active shell, or set '{env_var}' in your "
                "'.env' file, so the configured agent provider can authenticate."
            ),
            command=f'export {env_var}="<your-api-key>"',
        )
        for env_var in _credential_names(result.details)
    ]
    if result.details.get("provider") == "claude":
        remediations.append(_claude_login_remediation())

    return remediations


def _agent_model_missing(result: DiagnosticCheckRecord) -> list[Remediation]:
    """Return the remediation for an agent provider with no model configured (DOCTOR_AGENT_MODEL_MISSING)."""
    provider = result.details.get("provider", "<provider>")
    return [
        Remediation(
            message=(
                f"Run 'dovo config set agents.{provider}.model \"<model>\"' to set 'agents.{provider}.model' "
                "in '.dovo/config.json' to a model supported by that provider."
            ),
            command=f'dovo config set agents.{provider}.model "<model>"',
        )
    ]


_ERROR_CODE_BUILDERS: Final[dict[ErrorCode, Callable[[DiagnosticCheckRecord], list[Remediation]]]] = {
    ErrorCode.DOCTOR_GIT_REPO_NOT_FOUND: _git_repo_not_found,
    ErrorCode.DOCTOR_GIT_BINARY_MISSING: _git_binary_missing,
    ErrorCode.DOCTOR_GIT_TIMEOUT: _git_timeout,
    ErrorCode.DOCTOR_FS_UNWRITABLE: _fs_unwritable,
    ErrorCode.DOCTOR_WORKTREE_STALE: _worktree_stale,
    ErrorCode.DOCTOR_WORKTREE_ORPHANED: _worktree_orphaned,
    ErrorCode.DOCTOR_BINARY_MISSING: _binary_missing,
    ErrorCode.DOCTOR_AGENT_KEY_MISSING: _agent_key_missing,
    ErrorCode.DOCTOR_AGENT_MODEL_MISSING: _agent_model_missing,
}
