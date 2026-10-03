"""Unit tests for dovo.core.doctor.services.remediation."""

from __future__ import annotations

import pytest

from dovo.core.doctor.models import (
    CheckCategory,
    CheckStatus,
    DiagnosticCheckResult,
    Remediation,
    RemediationType,
)
from dovo.core.doctor.services.remediation import format_remediation_summary, resolve_remediations

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
                code="DOCTOR_GIT_NOT_REPO",
                title="Initialize Git repository",
                action_type=RemediationType.COMMAND,
                command="git init",
                description="Run `git init` from the workspace root to create the Git repository worktree operations require.",
                doc_path=None,
                is_automated=False,
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
                code="DOCTOR_GIT_BINARY_MISSING",
                title="Install Git",
                action_type=RemediationType.MANUAL,
                command=None,
                description="Install Git via your operating system's package manager and ensure the `git` executable is available on PATH.",
                doc_path=None,
                is_automated=False,
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
                code="DOCTOR_CONFIG_NOT_FOUND",
                title="Initialize Dovo workspace",
                action_type=RemediationType.COMMAND,
                command="dovo init",
                description="Run `dovo init` to create `.dovo/config.json` and the required workspace directory structure.",
                doc_path="docs/cli/init.md",
                is_automated=False,
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
            Remediation(
                code="DOCTOR_CONFIG_MALFORMED",
                title="Repair configuration file",
                action_type=RemediationType.COMMAND,
                command="dovo init --repair",
                description="Run `dovo init --repair` to restore missing schema keys, or edit `.dovo/config.json` by hand to fix the invalid JSON syntax.",
                doc_path="docs/cli/init.md",
                is_automated=False,
            )
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
                code="DOCTOR_CONFIG_SCHEMA_INVALID",
                title="Validate and repair configuration",
                action_type=RemediationType.COMMAND,
                command="dovo config validate",
                description="Run `dovo config validate` to see detailed schema errors, then fix `.dovo/config.json` or run `dovo init --repair` to restore missing defaults.",
                doc_path="docs/cli/config.md",
                is_automated=False,
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
                code="DOCTOR_FS_UNWRITABLE",
                title="Fix directory permissions",
                action_type=RemediationType.COMMAND,
                command="chmod u+w /repo/.dovo",
                description="Grant the current user write access to '/repo/.dovo' so Dovo can create and update workspace state.",
                doc_path=None,
                is_automated=False,
            ),
            Remediation(
                code="DOCTOR_FS_UNWRITABLE",
                title="Fix directory permissions",
                action_type=RemediationType.COMMAND,
                command="chmod u+w /repo/.dovo/sessions",
                description="Grant the current user write access to '/repo/.dovo/sessions' so Dovo can create and update workspace state.",
                doc_path=None,
                is_automated=False,
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
                code="DOCTOR_FS_UNWRITABLE",
                title="Fix directory permissions",
                action_type=RemediationType.COMMAND,
                command="chmod u+w .dovo",
                description="Grant the current user write access to '.dovo' so Dovo can create and update workspace state.",
                doc_path=None,
                is_automated=False,
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
                code="DOCTOR_WORKTREE_STALE",
                title="Prune stale worktrees",
                action_type=RemediationType.COMMAND,
                command="dovo worktree prune",
                description="Run `dovo worktree prune` to reconcile worktree database records that no longer match a live Git worktree.",
                doc_path="docs/cli/worktree.md",
                is_automated=False,
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
                code="DOCTOR_WORKTREE_ORPHAN",
                title="Prune orphan worktree directories",
                action_type=RemediationType.COMMAND,
                command="dovo worktree prune",
                description="Run `dovo worktree prune` to remove worktree directories that have no matching database record.",
                doc_path="docs/cli/worktree.md",
                is_automated=False,
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
            message="1 required binary(s) not found on PATH: gemini.",
            details={"missing_binaries": ["gemini"]},
            error_code="DOCTOR_BINARY_MISSING",
        ),
        [
            Remediation(
                code="DOCTOR_BINARY_MISSING",
                title="Install required binary",
                action_type=RemediationType.MANUAL,
                command=None,
                description="Install 'gemini' via your package manager and ensure it is available on PATH.",
                doc_path=None,
                is_automated=False,
            )
        ],
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
        [
            Remediation(
                code="DOCTOR_BINARY_MISSING",
                title="Install required binary",
                action_type=RemediationType.MANUAL,
                command=None,
                description="Install '<binary>' via your package manager and ensure it is available on PATH.",
                doc_path=None,
                is_automated=False,
            )
        ],
        id="binary_missing_missing_details_fallback",
    ),
    pytest.param(
        DiagnosticCheckResult(
            check_id="agent.setup",
            name="Agent Setup Check",
            category=CheckCategory.AGENT,
            status=CheckStatus.FAILED,
            message="Agent provider 'gemini' is missing required credential 'GEMINI_API_KEY'.",
            details={"provider": "gemini", "missing_env_var": "GEMINI_API_KEY"},
            error_code="DOCTOR_AGENT_KEY_MISSING",
        ),
        [
            Remediation(
                code="DOCTOR_AGENT_KEY_MISSING",
                title="Export agent API key",
                action_type=RemediationType.COMMAND,
                command='export GEMINI_API_KEY="<your-api-key>"',
                description="Set the GEMINI_API_KEY environment variable in your active shell or `.env` file so the configured agent provider can authenticate.",
                doc_path="docs/guides/agent-providers.md",
                is_automated=False,
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
                code="DOCTOR_AGENT_KEY_MISSING",
                title="Export agent API key",
                action_type=RemediationType.COMMAND,
                command='export <PROVIDER>_API_KEY="<your-api-key>"',
                description="Set the <PROVIDER>_API_KEY environment variable in your active shell or `.env` file so the configured agent provider can authenticate.",
                doc_path="docs/guides/agent-providers.md",
                is_automated=False,
            )
        ],
        id="agent_key_missing_missing_details_fallback",
    ),
    pytest.param(
        DiagnosticCheckResult(
            check_id="agent.setup",
            name="Agent Setup Check",
            category=CheckCategory.AGENT,
            status=CheckStatus.WARNING,
            message="Agent provider 'local' has no model configured.",
            details={"provider": "local"},
            error_code="DOCTOR_AGENT_NO_MODEL",
        ),
        [
            Remediation(
                code="DOCTOR_AGENT_NO_MODEL",
                title="Configure agent model",
                action_type=RemediationType.COMMAND,
                command='dovo config set agent.model "<model>"',
                description="Set `agent.model` in `.dovo/config.json` to a model supported by the configured provider, e.g. `dovo config set agent.model <model-name>`.",
                doc_path="docs/cli/config.md",
                is_automated=False,
            )
        ],
        id="agent_no_model",
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
                code="DOCTOR_UNKNOWN",
                title="Investigate check failure manually",
                action_type=RemediationType.MANUAL,
                command=None,
                description=(
                    "No deterministic remediation is registered for check 'worktree.refs' "
                    "(error_code='DOCTOR_UNKNOWN'). Review the check message and details to diagnose and resolve the issue."
                ),
                doc_path=None,
                is_automated=False,
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
                code="DOCTOR_CHECK_CRASH",
                title="Investigate check failure manually",
                action_type=RemediationType.MANUAL,
                command=None,
                description=(
                    "No deterministic remediation is registered for check 'crashing.check' "
                    "(error_code='DOCTOR_CHECK_CRASH'). Review the check message and details to diagnose and resolve the issue."
                ),
                doc_path=None,
                is_automated=False,
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
        """[tier-1/unit] resolve_remediations: returns the exact literal Remediation list for each parametrized DiagnosticCheckResult case."""
        assert resolve_remediations(result) == expected


FORMAT_SUMMARY_CASES = [
    pytest.param([], "", id="empty_list_returns_empty_string"),
    pytest.param(
        [
            Remediation(
                code="DOCTOR_GIT_NOT_REPO",
                title="Initialize Git repository",
                action_type=RemediationType.COMMAND,
                command="git init",
                description="Run `git init` ...",
                doc_path=None,
                is_automated=False,
            )
        ],
        "Fix:\n  • Initialize Git repository:\n    git init",
        id="single_command_remediation",
    ),
    pytest.param(
        [
            Remediation(
                code="DOCTOR_GIT_BINARY_MISSING",
                title="Install Git",
                action_type=RemediationType.MANUAL,
                command=None,
                description="Install Git via your operating system's package manager.",
                doc_path=None,
                is_automated=False,
            )
        ],
        "Fix:\n  • Install Git:\n    Install Git via your operating system's package manager.",
        id="single_manual_remediation_uses_description",
    ),
    pytest.param(
        [
            Remediation(
                code="DOCTOR_FS_UNWRITABLE",
                title="Fix directory permissions",
                action_type=RemediationType.COMMAND,
                command="chmod u+w /a",
                description="Grant write access to '/a'.",
                doc_path=None,
                is_automated=False,
            ),
            Remediation(
                code="DOCTOR_FS_UNWRITABLE",
                title="Fix directory permissions",
                action_type=RemediationType.COMMAND,
                command="chmod u+w /b",
                description="Grant write access to '/b'.",
                doc_path=None,
                is_automated=False,
            ),
        ],
        "Fix:\n  • Fix directory permissions:\n    chmod u+w /a\n  • Fix directory permissions:\n    chmod u+w /b",
        id="multiple_remediations_render_multiple_bullets",
    ),
]


class FormatRemediationSummaryTests:
    """Unit tests for format_remediation_summary text rendering."""

    @pytest.mark.parametrize(("remediations", "expected"), FORMAT_SUMMARY_CASES)
    def test_format_remediation_summary_returns_exact_text(
        self, remediations: list[Remediation], expected: str
    ) -> None:
        """[tier-1/unit] format_remediation_summary: returns the exact literal string for each parametrized remediation list."""
        assert format_remediation_summary(remediations) == expected
