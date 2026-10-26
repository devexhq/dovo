"""Safe prune execution service for stale worktrees and resources."""

from __future__ import annotations

import shutil
from pathlib import Path

from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord, error_diagnostics, record_exception
from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import WorkspacePaths
from dovo.core.db import SessionsRepository, WorktreesRepository, WorktreeStatus
from dovo.core.git.exceptions import GitNotFoundError
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import (
    PruneAction,
    PrunedItem,
    StaleWorktreeCategory,
    StaleWorktreeItem,
    WorktreePruneResult,
    WorktreePruneStatus,
)
from dovo.core.worktree.services.detector import WorktreeDetector


def _item_context(item: StaleWorktreeItem) -> dict[str, str | None]:
    """Return the diagnostic context of a prune action: worktree ID, branch and path."""
    return {
        "worktree_id": item.session_id,
        "branch": item.branch_name,
        "path": str(item.path) if item.path else None,
    }


def _record_item_failure(exc: Exception, context: dict[str, str | None]) -> list[DiagnosticRecord]:
    """Record exc at error level for worktree.prune with context and return its record as a diagnostics list."""
    return error_diagnostics(
        record_exception(exc, level=DiagnosticLevel.ERROR, operation="worktree.prune", context=context)
    )


def _record_recovered(exc: Exception, context: dict[str, str | None]) -> None:
    """Record exc at debug level for worktree.prune for a failure Dovo recovered from, skipping GitNotFoundError."""
    if isinstance(exc, GitNotFoundError):
        return

    record_exception(exc, level=DiagnosticLevel.DEBUG, operation="worktree.prune", context=context)


class WorktreePruner:
    """Safe cleanup executor for stale worktrees, orphaned directories, and temporary branches."""

    def __init__(
        self,
        paths: WorkspacePaths,
        db: WorktreesRepository,
        *,
        git: GitRunner,
        sessions_db: SessionsRepository | None = None,
    ) -> None:
        """Initialize pruner bound to the workspace paths and database.

        Args:
            paths: Resolved command-invocation workspace paths; orphans are found under paths.worktrees_dir.
            db: WorktreesRepository instance.
            git: Runner for every git command.
            sessions_db: Optional SessionsRepository instance for run liveness checks.
        """
        self.paths = paths
        self.path = paths.root_dir
        self.db = db
        self.sessions_db = sessions_db
        self.git = git
        self.detector = WorktreeDetector(paths, db, git=git, sessions_db=sessions_db)

    def _prune_stale_worktree_ref(
        self, item: StaleWorktreeItem, *, dry_run: bool
    ) -> tuple[PrunedItem, list[DiagnosticRecord]]:
        """Prune administrative worktree registrations."""
        if dry_run:
            return PrunedItem(
                category=item.category,
                identifier=item.identifier,
                action=PruneAction.PRUNED,
                path=item.path,
                branch_name=item.branch_name,
                reason=f"Would prune: {item.reason}" if item.reason else "Would prune",
            ), []

        try:
            self.git.worktree_prune(self.path)
            return PrunedItem(
                category=item.category,
                identifier=item.identifier,
                action=PruneAction.PRUNED,
                path=item.path,
                branch_name=item.branch_name,
                reason=item.reason,
            ), []
        except Exception as exc:
            return PrunedItem(
                category=item.category,
                identifier=item.identifier,
                action=PruneAction.FAILED,
                path=item.path,
                branch_name=item.branch_name,
                reason=item.reason,
                error=f"Failed to prune git worktree ref: {exc}",
            ), _record_item_failure(exc, _item_context(item))

    def _remove_orphaned_dir(
        self, dir_path: Path, *, context: dict[str, str | None]
    ) -> tuple[str | None, list[DiagnosticRecord]]:
        """Remove dir_path with git worktree remove, falling back to rmtree; record the fallback or its failure."""
        if not dir_path.exists():
            return None, []
        try:
            self.git.worktree_remove(self.path, dir_path, force=True)
            return None, []
        except Exception as exc:
            try:
                shutil.rmtree(dir_path)
            except Exception as rm_exc:
                return f"Failed to remove directory '{dir_path}': {rm_exc}", _record_item_failure(rm_exc, context)
            _record_recovered(exc, context)
            return None, []

    def _cleanup_orphaned_dir_and_db(self, item: StaleWorktreeItem) -> tuple[list[str], list[DiagnosticRecord]]:
        """Perform disk deletion and database status update for an orphaned directory."""
        errors: list[str] = []
        diagnostics: list[DiagnosticRecord] = []
        context = _item_context(item)
        if item.path is not None:
            dir_err, dir_diagnostics = self._remove_orphaned_dir(item.path, context=context)
            if dir_err:
                errors.append(dir_err)
            diagnostics.extend(dir_diagnostics)

        if item.session_id is not None:
            try:
                self.db.update_status(item.session_id, WorktreeStatus.CLEANED)
            except Exception as db_exc:
                errors.append(f"Failed to update database status for '{item.session_id}': {db_exc}")
                diagnostics.extend(_record_item_failure(db_exc, context))

        try:
            self.git.worktree_prune(self.path)
        except Exception as exc:
            _record_recovered(exc, context)

        return errors, diagnostics

    def _prune_orphaned_directory(
        self,
        item: StaleWorktreeItem,
        *,
        dry_run: bool,
        force: bool,
    ) -> tuple[PrunedItem, list[DiagnosticRecord]]:
        """Remove unindexed or non-active worktree directory safely."""
        if item.is_dirty and not force:
            return PrunedItem(
                category=item.category,
                identifier=item.identifier,
                action=PruneAction.SKIPPED,
                path=item.path,
                branch_name=item.branch_name,
                session_id=item.session_id,
                reason=f"Orphaned directory '{item.identifier}' contains uncommitted changes; use --force to delete",
            ), []

        if dry_run:
            return PrunedItem(
                category=item.category,
                identifier=item.identifier,
                action=PruneAction.PRUNED,
                path=item.path,
                branch_name=item.branch_name,
                session_id=item.session_id,
                reason=f"Would prune: {item.reason}" if item.reason else "Would prune",
            ), []

        errors, diagnostics = self._cleanup_orphaned_dir_and_db(item)
        if errors:
            return PrunedItem(
                category=item.category,
                identifier=item.identifier,
                action=PruneAction.FAILED,
                path=item.path,
                branch_name=item.branch_name,
                session_id=item.session_id,
                reason=item.reason,
                error="; ".join(errors),
            ), diagnostics

        return PrunedItem(
            category=item.category,
            identifier=item.identifier,
            action=PruneAction.PRUNED,
            path=item.path,
            branch_name=item.branch_name,
            session_id=item.session_id,
            reason=item.reason,
        ), diagnostics

    def _prune_stale_db_record(
        self, item: StaleWorktreeItem, *, dry_run: bool
    ) -> tuple[PrunedItem, list[DiagnosticRecord]]:
        """Reconcile active database rows missing on disk."""
        if dry_run:
            return PrunedItem(
                category=item.category,
                identifier=item.identifier,
                action=PruneAction.PRUNED,
                path=item.path,
                branch_name=item.branch_name,
                session_id=item.session_id,
                reason=f"Would prune: {item.reason}" if item.reason else "Would prune",
            ), []

        try:
            self.db.update_status(item.identifier, WorktreeStatus.CLEANED)
            return PrunedItem(
                category=item.category,
                identifier=item.identifier,
                action=PruneAction.PRUNED,
                path=item.path,
                branch_name=item.branch_name,
                session_id=item.session_id,
                reason=item.reason,
            ), []
        except Exception as exc:
            return PrunedItem(
                category=item.category,
                identifier=item.identifier,
                action=PruneAction.FAILED,
                path=item.path,
                branch_name=item.branch_name,
                session_id=item.session_id,
                reason=item.reason,
                error=f"Failed to update database status for '{item.identifier}': {exc}",
            ), _record_item_failure(exc, _item_context(item))

    def _prune_stale_branch(
        self, item: StaleWorktreeItem, *, dry_run: bool
    ) -> tuple[PrunedItem, list[DiagnosticRecord]]:
        """Delete unlinked temporary worktree branches."""
        if dry_run:
            return PrunedItem(
                category=item.category,
                identifier=item.identifier,
                action=PruneAction.PRUNED,
                branch_name=item.branch_name,
                reason=f"Would prune: {item.reason}" if item.reason else "Would prune",
            ), []

        try:
            self.git.branch_delete(self.path, item.identifier, force=True)
            return PrunedItem(
                category=item.category,
                identifier=item.identifier,
                action=PruneAction.PRUNED,
                branch_name=item.branch_name,
                reason=item.reason,
            ), []
        except Exception as exc:
            return PrunedItem(
                category=item.category,
                identifier=item.identifier,
                action=PruneAction.FAILED,
                branch_name=item.branch_name,
                reason=item.reason,
                error=f"Failed to delete branch '{item.identifier}': {exc}",
            ), _record_item_failure(exc, _item_context(item))

    def _process_item(
        self,
        item: StaleWorktreeItem,
        *,
        dry_run: bool,
        force: bool,
    ) -> tuple[PrunedItem, list[DiagnosticRecord]]:
        """Route stale item to the appropriate category handler, returning the item and its error-level records."""
        if item.category == StaleWorktreeCategory.STALE_WORKTREE_REF:
            return self._prune_stale_worktree_ref(item, dry_run=dry_run)
        elif item.category == StaleWorktreeCategory.ORPHANED_DIRECTORY:
            return self._prune_orphaned_directory(item, dry_run=dry_run, force=force)
        elif item.category == StaleWorktreeCategory.STALE_DB_RECORD:
            return self._prune_stale_db_record(item, dry_run=dry_run)
        elif item.category == StaleWorktreeCategory.STALE_BRANCH:
            return self._prune_stale_branch(item, dry_run=dry_run)
        return PrunedItem(
            category=item.category,
            identifier=item.identifier,
            action=PruneAction.SKIPPED,
            reason=f"Unknown category '{item.category}'",
        ), []

    def prune(
        self,
        *,
        dry_run: bool = False,
        force: bool = False,
    ) -> WorktreePruneResult:
        """Prune detected stale resources, or preview them in dry-run mode; the caller holds the workspace lock when not dry_run.

        Args:
            dry_run: When True, preview actions without mutating filesystem or DB.
            force: When True, delete dirty orphaned directories containing uncommitted files.

        Returns:
            Structured WorktreePruneResult summarizing processed items.
        """
        detection = self.detector.detect()
        processed_items: list[PrunedItem] = []
        item_diagnostics: list[DiagnosticRecord] = []
        errors: list[str] = []

        for stale_item in detection.items:
            res, res_diagnostics = self._process_item(stale_item, dry_run=dry_run, force=force)
            processed_items.append(res)
            item_diagnostics.extend(res_diagnostics)
            if res.error:
                errors.append(res.error)

        return WorktreePruneResult(
            status=WorktreePruneStatus.PARTIAL_FAILURE if errors else WorktreePruneStatus.OK,
            dry_run=dry_run,
            force=force,
            items=processed_items,
            error_code=ErrorCode.WORKTREE_PRUNE_ITEMS_FAILED if errors else None,
            errors=errors,
            warnings=list(detection.warnings),
            diagnostics=[*detection.diagnostics, *item_diagnostics],
        )
