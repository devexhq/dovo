"""Unit tests for dovo.core.diagnostics.services.remediation."""

from __future__ import annotations

import pytest

from dovo.common.models import Remediation
from dovo.core.diagnostics.models import CheckCategory, CheckStatus, DiagnosticCheckResult
from dovo.core.diagnostics.services.remediation import resolve_remediations


def _export_remediation(env_var: str) -> Remediation:
    """Return the DOCTOR_AGENT_KEY_MISSING export remediation for one credential name."""
    return Remediation(
        message=(
            f"Run 'export {env_var}=\"<your-api-key>\"' in your active shell, or set '{env_var}' in your "
            "'.env' file, so the configured agent provider can authenticate."
        ),
        command=f'export {env_var}="<your-api-key>"',
    )


REMEDIATION_RESOLUTION_CASES = [
    pytest.param(
        DiagnosticCheckResult(
            check_id="git.repo",
            name="Git Repository Check",
            category=CheckCategory.GIT,
            status=CheckStatus.OK,
            message="ok",
        ),
        [],
        id="ok_status_returns_empty",
    ),
    pytest.param(
        DiagnosticCheckResult(
            check_id="worktree.refs",
            name="Worktree References Check",
            category=CheckCategory.WORKTREE,
            status=CheckStatus.SKIPPED,
            message="Check 'worktree.refs' skipped by configuration.",
        ),
        [],
        id="skipped_status_returns_empty",
    ),
    pytest.param(
        DiagnosticCheckResult(
            check_id="git.repo",
            name="Git Repository Check",
            category=CheckCategory.GIT,
            status=CheckStatus.FAILED,
            message="'/tmp/x' is not a Git repository.",
            error_code="DOCTOR_GIT_NOT_REPO",
        ),
        [
            Remediation(
                message="Run 'git init' from the workspace root to create the Git repository worktree operations require.",
                command="git init",
            )
        ],
        id="git_not_repo",
    ),
    pytest.param(
        DiagnosticCheckResult(
            check_id="git.repo",
            name="Git Repository Check",
            category=CheckCategory.GIT,
            status=CheckStatus.FAILED,
            message="git binary was not found on PATH.",
            error_code="DOCTOR_GIT_BINARY_MISSING",
        ),
        [
            Remediation(
                message="Install Git via your operating system's package manager and ensure the 'git' executable is available on 'PATH'."
            )
        ],
        id="git_binary_missing",
    ),
    pytest.param(
        DiagnosticCheckResult(
            check_id="config.schema",
            name="Config Schema Check",
            category=CheckCategory.CONFIG,
            status=CheckStatus.FAILED,
            message="Configuration file not found.",
            error_code="DOCTOR_CONFIG_NOT_FOUND",
        ),
        [
            Remediation(
                message="Run 'dovo init' to create '.dovo/config.json' and the required workspace directory structure.",
                command="dovo init",
            )
        ],
        id="config_not_found",
    ),
    pytest.param(
        DiagnosticCheckResult(
            check_id="config.schema",
            name="Config Schema Check",
            category=CheckCategory.CONFIG,
            status=CheckStatus.FAILED,
            message="Invalid JSON.",
            error_code="DOCTOR_CONFIG_MALFORMED",
        ),
        [
            Remediation(message="Edit '.dovo/config.json' by hand to fix the invalid JSON syntax."),
            Remediation(
                message="Run 'dovo init --overwrite' to replace it with a default configuration, discarding its contents.",
                command="dovo init --overwrite",
            ),
        ],
        id="config_malformed",
    ),
    pytest.param(
        DiagnosticCheckResult(
            check_id="config.schema",
            name="Config Schema Check",
            category=CheckCategory.CONFIG,
            status=CheckStatus.FAILED,
            message="Schema invalid.",
            error_code="DOCTOR_CONFIG_SCHEMA_INVALID",
        ),
        [
            Remediation(
                message="Run 'dovo config validate' to see detailed schema errors, then fix '.dovo/config.json' or run 'dovo init --repair' to restore missing defaults.",
                command="dovo config validate",
            )
        ],
        id="config_schema_invalid",
    ),
    pytest.param(
        DiagnosticCheckResult(
            check_id="filesystem.writable",
            name="Filesystem Writable Check",
            category=CheckCategory.FILESYSTEM,
            status=CheckStatus.FAILED,
            message="2 configured path(s) are not writable.",
            details={"unwritable_paths": ["/repo/.dovo", "/repo/.dovo/sessions"]},
            error_code="DOCTOR_FS_UNWRITABLE",
        ),
        [
            Remediation(
                message="Run 'chmod u+w /repo/.dovo' to grant the current user write access to '/repo/.dovo' so Dovo can create and update workspace state.",
                command="chmod u+w /repo/.dovo",
            ),
            Remediation(
                message="Run 'chmod u+w /repo/.dovo/sessions' to grant the current user write access to '/repo/.dovo/sessions' so Dovo can create and update workspace state.",
                command="chmod u+w /repo/.dovo/sessions",
            ),
        ],
        id="fs_unwritable_with_paths",
    ),
    pytest.param(
        DiagnosticCheckResult(
            check_id="filesystem.writable",
            name="Filesystem Writable Check",
            category=CheckCategory.FILESYSTEM,
            status=CheckStatus.FAILED,
            message="paths are not writable.",
            error_code="DOCTOR_FS_UNWRITABLE",
        ),
        [
            Remediation(
                message="Run 'chmod u+w .dovo' to grant the current user write access to '.dovo' so Dovo can create and update workspace state.",
                command="chmod u+w .dovo",
            ),
        ],
        id="fs_unwritable_missing_details_fallback",
    ),
    pytest.param(
        DiagnosticCheckResult(
            check_id="worktree.refs",
            name="Worktree References Check",
            category=CheckCategory.WORKTREE,
            status=CheckStatus.WARNING,
            message="1 stale worktree reference(s) detected.",
            error_code="DOCTOR_WORKTREE_STALE",
        ),
        [
            Remediation(
                message="Run 'dovo worktree prune' to reconcile worktree database records that no longer match a live Git worktree.",
                command="dovo worktree prune",
            )
        ],
        id="worktree_stale",
    ),
    pytest.param(
        DiagnosticCheckResult(
            check_id="worktree.refs",
            name="Worktree References Check",
            category=CheckCategory.WORKTREE,
            status=CheckStatus.WARNING,
            message="1 orphaned worktree directory(s) detected.",
            error_code="DOCTOR_WORKTREE_ORPHAN",
        ),
        [
            Remediation(
                message="Run 'dovo worktree prune' to remove worktree directories that have no matching database record.",
                command="dovo worktree prune",
            )
        ],
        id="worktree_orphan",
    ),
    pytest.param(
        DiagnosticCheckResult(
            check_id="env.binaries",
            name="Environment Binaries Check",
            category=CheckCategory.ENVIRONMENT,
            status=CheckStatus.WARNING,
            message="1 required binary(s) not found on PATH: gh.",
            details={"missing_binaries": ["gh"]},
            error_code="DOCTOR_BINARY_MISSING",
        ),
        [Remediation(message="Install 'gh' via your package manager and ensure it is available on 'PATH'.")],
        id="binary_missing_with_details",
    ),
    pytest.param(
        DiagnosticCheckResult(
            check_id="env.binaries",
            name="Environment Binaries Check",
            category=CheckCategory.ENVIRONMENT,
            status=CheckStatus.WARNING,
            message="required binary(s) not found on PATH.",
            error_code="DOCTOR_BINARY_MISSING",
        ),
        [Remediation(message="Install '<binary>' via your package manager and ensure it is available on 'PATH'.")],
        id="binary_missing_missing_details_fallback",
    ),
    pytest.param(
        DiagnosticCheckResult(
            check_id="agent.setup",
            name="Agent Setup Check",
            category=CheckCategory.AGENT,
            status=CheckStatus.FAILED,
            message="Agent provider 'copilot' is missing required credential 'GH_TOKEN'.",
            details={"provider": "copilot", "missing_env_var": "GH_TOKEN"},
            error_code="DOCTOR_AGENT_KEY_MISSING",
        ),
        [
            Remediation(
                message="Run 'export GH_TOKEN=\"<your-api-key>\"' in your active shell, or set 'GH_TOKEN' in your '.env' file, so the configured agent provider can authenticate.",
                command='export GH_TOKEN="<your-api-key>"',
            )
        ],
        id="agent_key_missing_with_details",
    ),
    pytest.param(
        DiagnosticCheckResult(
            check_id="agent.setup",
            name="Agent Setup Check",
            category=CheckCategory.AGENT,
            status=CheckStatus.FAILED,
            message="missing required credential.",
            error_code="DOCTOR_AGENT_KEY_MISSING",
        ),
        [
            Remediation(
                message="Run 'export <PROVIDER>_API_KEY=\"<your-api-key>\"' in your active shell, or set '<PROVIDER>_API_KEY' in your '.env' file, so the configured agent provider can authenticate.",
                command='export <PROVIDER>_API_KEY="<your-api-key>"',
            )
        ],
        id="agent_key_missing_missing_details_fallback",
    ),
    pytest.param(
        DiagnosticCheckResult(
            check_id="agent.setup",
            name="Agent Setup Check",
            category=CheckCategory.AGENT,
            status=CheckStatus.FAILED,
            message="Agent provider 'claude' is missing required credential 'CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY'.",
            details={
                "provider": "claude",
                "missing_env_var": "CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY",
                "credential_envs": ["CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"],
            },
            error_code="DOCTOR_AGENT_KEY_MISSING",
        ),
        [
            _export_remediation("CLAUDE_CODE_OAUTH_TOKEN"),
            _export_remediation("ANTHROPIC_API_KEY"),
            Remediation(
                message="Run 'claude auth login', or export 'CLAUDE_CODE_OAUTH_TOKEN' from 'claude setup-token', so Claude Code authenticates without an API key."
            ),
        ],
        id="agent_key_missing_claude_exports_each_name_then_login",
    ),
    pytest.param(
        DiagnosticCheckResult(
            check_id="agent.setup",
            name="Agent Setup Check",
            category=CheckCategory.AGENT,
            status=CheckStatus.FAILED,
            message="Agent provider 'copilot' is missing required credential 'GH_TOKEN or GITHUB_TOKEN'.",
            details={
                "provider": "copilot",
                "missing_env_var": "GH_TOKEN or GITHUB_TOKEN",
                "credential_envs": ["GH_TOKEN", "GITHUB_TOKEN"],
            },
            error_code="DOCTOR_AGENT_KEY_MISSING",
        ),
        [_export_remediation("GH_TOKEN"), _export_remediation("GITHUB_TOKEN")],
        id="agent_key_missing_two_names_emit_one_export_each",
    ),
    pytest.param(
        DiagnosticCheckResult(
            check_id="agent.setup",
            name="Agent Setup Check",
            category=CheckCategory.AGENT,
            status=CheckStatus.FAILED,
            message="Agent provider 'copilot' is missing required credential 'GH_TOKEN'.",
            details={"provider": "copilot", "missing_env_var": "GH_TOKEN", "credential_envs": "GH_TOKEN"},
            error_code="DOCTOR_AGENT_KEY_MISSING",
        ),
        [_export_remediation("GH_TOKEN")],
        id="agent_key_missing_non_list_credential_envs_falls_back_to_missing_env_var",
    ),
    pytest.param(
        DiagnosticCheckResult(
            check_id="agent.setup",
            name="Agent Setup Check",
            category=CheckCategory.AGENT,
            status=CheckStatus.WARNING,
            message="Agent provider 'copilot' has no model configured.",
            details={"provider": "copilot"},
            error_code="DOCTOR_AGENT_NO_MODEL",
        ),
        [
            Remediation(
                message="Run 'dovo config set agents.copilot.model \"<model>\"' to set 'agents.copilot.model' in '.dovo/config.json' to a model supported by that provider.",
                command='dovo config set agents.copilot.model "<model>"',
            )
        ],
        id="agent_no_model",
    ),
    pytest.param(
        DiagnosticCheckResult(
            check_id="agent.setup",
            name="Agent Setup Check",
            category=CheckCategory.AGENT,
            status=CheckStatus.WARNING,
            message="Agent provider has no model configured.",
            error_code="DOCTOR_AGENT_NO_MODEL",
        ),
        [
            Remediation(
                message="Run 'dovo config set agents.<provider>.model \"<model>\"' to set 'agents.<provider>.model' in '.dovo/config.json' to a model supported by that provider.",
                command='dovo config set agents.<provider>.model "<model>"',
            )
        ],
        id="agent_no_model_missing_details_fallback",
    ),
    pytest.param(
        DiagnosticCheckResult(
            check_id="worktree.refs",
            name="Worktree References Check",
            category=CheckCategory.WORKTREE,
            status=CheckStatus.WARNING,
            message="Worktree detection could not complete (status='git_failed').",
            details={"detection_status": "git_failed"},
        ),
        [
            Remediation(
                message="No deterministic remediation is registered for check 'worktree.refs' (error_code='DOCTOR_UNKNOWN'). Review the check message and details to diagnose and resolve the issue."
            )
        ],
        id="none_error_code_falls_back",
    ),
    pytest.param(
        DiagnosticCheckResult(
            check_id="crashing.check",
            name="Crashing Check",
            category=CheckCategory.WORKTREE,
            status=CheckStatus.FAILED,
            message="Unhandled exception during check execution: boom",
            error_code="DOCTOR_CHECK_CRASH",
        ),
        [
            Remediation(
                message="No deterministic remediation is registered for check 'crashing.check' (error_code='DOCTOR_CHECK_CRASH'). Review the check message and details to diagnose and resolve the issue."
            )
        ],
        id="unmapped_string_code_falls_back",
    ),
]


class ResolveRemediationsTests:
    """Unit tests for resolve_remediations deterministic mapping."""

    @pytest.mark.parametrize(("result", "expected"), REMEDIATION_RESOLUTION_CASES)
    def test_resolve_remediations_returns_exact_mapping(
        self, result: DiagnosticCheckResult, expected: list[Remediation]
    ) -> None:
        assert resolve_remediations(result) == expected
