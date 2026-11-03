"""Deterministic remediation resolution for failing and warning diagnostic checks."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

from dovo.common.models import Remediation
from dovo.core.diagnostics.models import CheckStatus, DiagnosticCheckResult


def resolve_remediations(result: DiagnosticCheckResult) -> list[Remediation]:
    """Return a failed or warning check's own remediations, or the deterministic ones for its error code."""
    if result.status not in (CheckStatus.FAILED, CheckStatus.WARNING):
        return []
    if result.remediations:
        return list(result.remediations)
    builder = _ERROR_CODE_BUILDERS.get(result.error_code or "")
    if builder is None:
        return [_fallback_remediation(result)]
    return builder(result)


def _fallback_remediation(result: DiagnosticCheckResult) -> Remediation:
    """Return the generic manual-inspection remediation for a check with no mapped error code."""
    code = result.error_code or "DOCTOR_UNKNOWN"
    return Remediation(
        message=(
            f"No deterministic remediation is registered for check '{result.check_id}' "
            f"(error_code='{code}'). Review the check message and details to diagnose and resolve the issue."
        ),
    )


def _git_not_repo(result: DiagnosticCheckResult) -> list[Remediation]:
    """Return the remediation for a workspace that is not a Git repository (DOCTOR_GIT_NOT_REPO)."""
    return [
        Remediation(
            message="Run 'git init' from the workspace root to create the Git repository worktree operations require.",
            command="git init",
        )
    ]


def _git_binary_missing(result: DiagnosticCheckResult) -> list[Remediation]:
    """Return the remediation for a missing git executable on PATH (DOCTOR_GIT_BINARY_MISSING)."""
    return [
        Remediation(
            message=(
                "Install Git via your operating system's package manager and ensure the 'git' executable "
                "is available on 'PATH'."
            ),
        )
    ]


def _fs_unwritable(result: DiagnosticCheckResult) -> list[Remediation]:
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


def _worktree_stale(result: DiagnosticCheckResult) -> list[Remediation]:
    """Return the remediation for stale worktree database references (DOCTOR_WORKTREE_STALE)."""
    return [
        Remediation(
            message="Run 'dovo worktree prune' to reconcile worktree database records that no longer match a live Git worktree.",
            command="dovo worktree prune",
        )
    ]


def _worktree_orphan(result: DiagnosticCheckResult) -> list[Remediation]:
    """Return the remediation for orphaned worktree directories (DOCTOR_WORKTREE_ORPHAN)."""
    return [
        Remediation(
            message="Run 'dovo worktree prune' to remove worktree directories that have no matching database record.",
            command="dovo worktree prune",
        )
    ]


def _binary_missing(result: DiagnosticCheckResult) -> list[Remediation]:
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


def _agent_key_missing(result: DiagnosticCheckResult) -> list[Remediation]:
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


def _agent_no_model(result: DiagnosticCheckResult) -> list[Remediation]:
    """Return the remediation for an agent provider with no model configured (DOCTOR_AGENT_NO_MODEL)."""
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


_ERROR_CODE_BUILDERS: Final[dict[str, Callable[[DiagnosticCheckResult], list[Remediation]]]] = {
    "DOCTOR_GIT_NOT_REPO": _git_not_repo,
    "DOCTOR_GIT_BINARY_MISSING": _git_binary_missing,
    "DOCTOR_FS_UNWRITABLE": _fs_unwritable,
    "DOCTOR_WORKTREE_STALE": _worktree_stale,
    "DOCTOR_WORKTREE_ORPHAN": _worktree_orphan,
    "DOCTOR_BINARY_MISSING": _binary_missing,
    "DOCTOR_AGENT_KEY_MISSING": _agent_key_missing,
    "DOCTOR_AGENT_NO_MODEL": _agent_no_model,
}
