"""Diagnostic check validating Git binary presence and repository validity."""

from __future__ import annotations

import shutil
from pathlib import Path

from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord, error_diagnostics, record_exception
from dovo.common.error_codes import ErrorCode
from dovo.core.diagnostics.models import CheckCategory, CheckStatus, DiagnosticCheckRecord, DiagnosticsContext
from dovo.core.git import GitError

_BINARY_MISSING_MESSAGE = "git binary was not found on PATH."


class GitRepoCheck:
    """Diagnostic check validating Git binary presence and repository validity."""

    check_id: str = "git.repo"
    name: str = "Git Repository Check"
    category: CheckCategory = CheckCategory.GIT

    def execute(self, context: DiagnosticsContext) -> DiagnosticCheckRecord:
        """Validate the git binary is on PATH and context.cwd is a Git repository with a resolvable HEAD.

        A git timeout or a git binary that vanishes after the PATH lookup fails the check under its own code with
        the record of the caught GitError in diagnostics; a non-zero git exit means context.cwd is not a repository.

        Raises:
            GitError: With GIT_WORKDIR_MISSING when context.cwd is not a directory.
        """
        if shutil.which("git") is None:
            return self._failed(ErrorCode.DOCTOR_GIT_BINARY_MISSING, _BINARY_MISSING_MESSAGE)

        try:
            return self._probe(context)
        except GitError as exc:
            return self._git_error_result(exc, context.cwd)

    def _probe(self, context: DiagnosticsContext) -> DiagnosticCheckRecord:
        """Return the OK result with the repository root and branch, or the not-a-repo failure when git answers no."""
        is_inside_work_tree = context.git.run(["rev-parse", "--is-inside-work-tree"], path=context.cwd)
        if is_inside_work_tree != "true":
            return self._not_repo(context.cwd)

        root = context.git.run(["rev-parse", "--show-toplevel"], path=context.cwd)
        branch = context.git.get_current_branch(context.cwd)
        message = f"Git repository detected at '{root}' on branch '{branch}'."
        return DiagnosticCheckRecord(
            check_id=self.check_id,
            name=self.name,
            category=self.category,
            status=CheckStatus.OK,
            message=message,
            details={"root": root, "branch": branch},
            error_code=None,
            errors=[],
            warnings=[],
        )

    def _git_error_result(self, exc: GitError, cwd: Path) -> DiagnosticCheckRecord:
        """Map a caught GitError to the failure that names its cause, recording every cause but a non-zero git exit.

        Raises:
            GitError: exc itself when its code is none of GIT_FAILED, GIT_BINARY_MISSING or GIT_TIMEOUT.
        """
        if exc.error_code is ErrorCode.GIT_FAILED:
            return self._not_repo(cwd)
        if exc.error_code not in (ErrorCode.GIT_BINARY_MISSING, ErrorCode.GIT_TIMEOUT):
            raise exc

        record = record_exception(
            exc, level=DiagnosticLevel.ERROR, operation="diagnostics.git_repo", context={"path": str(cwd)}
        )
        if exc.error_code is ErrorCode.GIT_BINARY_MISSING:
            return self._failed(ErrorCode.DOCTOR_GIT_BINARY_MISSING, _BINARY_MISSING_MESSAGE, record)
        return self._failed(ErrorCode.DOCTOR_GIT_TIMEOUT, f"Git timed out in '{cwd}': {exc.message}", record)

    def _not_repo(self, cwd: Path) -> DiagnosticCheckRecord:
        """Build the FAILED result for a directory that is not a valid Git repository."""
        return self._failed(ErrorCode.DOCTOR_GIT_REPO_NOT_FOUND, f"'{cwd}' is not a Git repository.")

    def _failed(
        self, error_code: ErrorCode, message: str, record: DiagnosticRecord | None = None
    ) -> DiagnosticCheckRecord:
        """Build the FAILED result for error_code and message, listing record in diagnostics when it is error level."""
        return DiagnosticCheckRecord(
            check_id=self.check_id,
            name=self.name,
            category=self.category,
            status=CheckStatus.FAILED,
            message=message,
            details={},
            error_code=error_code,
            errors=[message],
            warnings=[],
            diagnostics=error_diagnostics(record),
        )
