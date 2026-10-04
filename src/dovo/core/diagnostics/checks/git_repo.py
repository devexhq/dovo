"""Diagnostic check validating Git binary presence and repository validity."""

from __future__ import annotations

import shutil
from pathlib import Path

from dovo.core.diagnostics.models import CheckCategory, CheckStatus, DiagnosticCheckResult, DiagnosticsContext
from dovo.core.git import (
    GitCommandError,
    GitNotFoundError,
    GitPlumbingTimeoutError,
    GitRunner,
)


class GitRepoCheck:
    """Diagnostic check validating Git binary presence and repository validity."""

    check_id: str = "git.repo"
    name: str = "Git Repository Check"
    category: CheckCategory = CheckCategory.GIT

    def execute(self, context: DiagnosticsContext) -> DiagnosticCheckResult:
        """Validate the git binary is on PATH and context.cwd is a Git repository with a resolvable HEAD."""
        if shutil.which("git") is None:
            return _binary_missing_result(self.check_id, self.name, self.category)

        try:
            is_inside_work_tree = GitRunner.run(["rev-parse", "--is-inside-work-tree"], path=context.cwd)
        except GitNotFoundError:
            return _binary_missing_result(self.check_id, self.name, self.category)
        except (GitCommandError, GitPlumbingTimeoutError):
            return _not_repo_result(self.check_id, self.name, self.category, context.cwd)

        if is_inside_work_tree != "true":
            return _not_repo_result(self.check_id, self.name, self.category, context.cwd)

        try:
            root = GitRunner.run(["rev-parse", "--show-toplevel"], path=context.cwd)
        except (GitNotFoundError, GitCommandError, GitPlumbingTimeoutError):
            return _not_repo_result(self.check_id, self.name, self.category, context.cwd)

        branch = GitRunner.get_current_branch(context.cwd)

        return _ok_result(self.check_id, self.name, self.category, root, branch)


def _binary_missing_result(check_id: str, name: str, category: CheckCategory) -> DiagnosticCheckResult:
    """Build the FAILED result for a missing git executable on PATH."""
    message = "git binary was not found on PATH."
    return DiagnosticCheckResult(
        check_id=check_id,
        name=name,
        category=category,
        status=CheckStatus.FAILED,
        message=message,
        details={},
        duration_ms=0.0,
        error_code="DOCTOR_GIT_BINARY_MISSING",
        errors=[message],
        warnings=[],
        fixes=[],
    )


def _not_repo_result(check_id: str, name: str, category: CheckCategory, cwd: Path) -> DiagnosticCheckResult:
    """Build the FAILED result for a directory that is not a valid Git repository."""
    message = f"'{cwd}' is not a Git repository."
    return DiagnosticCheckResult(
        check_id=check_id,
        name=name,
        category=category,
        status=CheckStatus.FAILED,
        message=message,
        details={},
        duration_ms=0.0,
        error_code="DOCTOR_GIT_NOT_REPO",
        errors=[message],
        warnings=[],
        fixes=[],
    )


def _ok_result(
    check_id: str,
    name: str,
    category: CheckCategory,
    root: str,
    branch: str,
) -> DiagnosticCheckResult:
    """Build the OK result carrying the resolved repository root and active branch."""
    message = f"Git repository detected at '{root}' on branch '{branch}'."
    return DiagnosticCheckResult(
        check_id=check_id,
        name=name,
        category=category,
        status=CheckStatus.OK,
        message=message,
        details={"root": root, "branch": branch},
        duration_ms=0.0,
        error_code=None,
        errors=[],
        warnings=[],
        fixes=[],
    )
