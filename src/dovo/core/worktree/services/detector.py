"""Stale worktree detection and resource inspection service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord, error_diagnostics, record_exception
from dovo.common.filesystem import WorkspacePaths
from dovo.core.db import SessionsRepository, WorktreeRecord, WorktreesRepository, WorktreeStatus
from dovo.core.git.exceptions import GitError, GitNotFoundError
from dovo.core.git.models import GitWorktreeEntry
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import (
    StaleWorktreeCategory,
    StaleWorktreeItem,
    WorktreeDetection,
)


@dataclass(frozen=True, slots=True)
class _DirtyState:
    """Dirty state of one orphaned directory, with the warning and error-level records from a failed inspection."""

    is_dirty: bool
    dirty_file_count: int
    warning: str | None
    diagnostics: tuple[DiagnosticRecord, ...]


def _build_record_lookups(
    all_records: list[WorktreeRecord],
) -> tuple[dict[str, WorktreeRecord], dict[str, WorktreeRecord]]:
    """Index worktree records by ID and resolved string path."""
    by_id: dict[str, WorktreeRecord] = {}
    by_path: dict[str, WorktreeRecord] = {}
    for r in all_records:
        by_id[r.id] = r
        by_path[str(Path(r.worktree_path).resolve())] = r
    return by_id, by_path


class WorktreeDetector:
    """Non-destructive scanner for stale worktrees, orphaned directories, and dead records."""

    def __init__(
        self,
        paths: WorkspacePaths,
        db: WorktreesRepository,
        *,
        git: GitRunner,
        sessions_db: SessionsRepository | None = None,
    ) -> None:
        """Initialize detector bound to the workspace paths and database.

        Args:
            paths: Resolved command-invocation workspace paths; worktrees are scanned under paths.worktrees_dir.
            db: WorktreesRepository instance.
            git: Runner for every git command.
            sessions_db: Optional SessionsRepository instance for run liveness checks.
        """
        self.paths = paths
        self.path = paths.root_dir
        self.worktree_base_dir = paths.worktrees_dir
        self.db = db
        self.sessions_db = sessions_db
        self.git = git

    def _detect_stale_worktree_refs(self, worktree_entries: list[GitWorktreeEntry]) -> list[StaleWorktreeItem]:
        """Identify registered git worktree refs pointing to missing paths or prunable."""
        stale_items: list[StaleWorktreeItem] = []
        for entry in worktree_entries:
            if entry.path.resolve() == self.path:
                continue
            if entry.is_prunable or not entry.path.is_dir():
                reason = (
                    entry.prunable_reason
                    if entry.prunable_reason
                    else f"Git worktree registration path '{entry.path}' is missing or prunable"
                )
                stale_items.append(
                    StaleWorktreeItem(
                        category=StaleWorktreeCategory.STALE_WORKTREE_REF,
                        identifier=str(entry.path),
                        path=entry.path,
                        branch_name=entry.branch,
                        reason=reason,
                    )
                )
        return stale_items

    def _detect_stale_db_records(self, all_records: list[WorktreeRecord]) -> list[StaleWorktreeItem]:
        """Identify active worktree database rows whose directory is missing on disk."""
        stale_items: list[StaleWorktreeItem] = []
        for record in all_records:
            if record.status == WorktreeStatus.ACTIVE:
                worktree_path = Path(record.worktree_path)
                if not worktree_path.is_dir():
                    stale_items.append(
                        StaleWorktreeItem(
                            category=StaleWorktreeCategory.STALE_DB_RECORD,
                            identifier=record.id,
                            session_id=record.id,
                            path=worktree_path,
                            branch_name=record.branch_name,
                            reason=f"Active database record '{record.id}' has missing worktree path on disk",
                        )
                    )
        return stale_items

    def _inspect_directory_dirty_state(self, dir_path: Path) -> _DirtyState:
        """Check whether an orphaned directory contains uncommitted or untracked changes."""
        try:
            status_lines = self.git.status_porcelain(dir_path)
        except Exception as exc:
            warning = f"Unable to inspect git status for orphaned directory '{dir_path.name}': {exc}"
            if isinstance(exc, GitError):
                return _DirtyState(is_dirty=False, dirty_file_count=0, warning=warning, diagnostics=())
            record = record_exception(
                exc, level=DiagnosticLevel.ERROR, operation="worktree.detect", context={"path": str(dir_path)}
            )
            return _DirtyState(
                is_dirty=False, dirty_file_count=0, warning=warning, diagnostics=tuple(error_diagnostics(record))
            )
        return _DirtyState(
            is_dirty=bool(status_lines), dirty_file_count=len(status_lines), warning=None, diagnostics=()
        )

    def _create_orphaned_item(
        self,
        child: Path,
        record: WorktreeRecord | None,
    ) -> tuple[StaleWorktreeItem, _DirtyState]:
        """Build StaleWorktreeItem and the dirty-state inspection outcome for an orphaned directory."""
        dirty = self._inspect_directory_dirty_state(child)
        if record is None:
            reason = f"Worktree directory '{child.name}' is not tracked in the database"
        else:
            reason = f"Worktree directory '{child.name}' has database status '{record.status.value}'"

        item = StaleWorktreeItem(
            category=StaleWorktreeCategory.ORPHANED_DIRECTORY,
            identifier=child.name,
            session_id=record.id if record else None,
            path=child,
            branch_name=record.branch_name if record else None,
            is_dirty=dirty.is_dirty,
            dirty_file_count=dirty.dirty_file_count,
            reason=reason,
        )
        return item, dirty

    def _process_worktree_child(
        self,
        child: Path,
        records_by_id: dict[str, WorktreeRecord],
        records_by_path: dict[str, WorktreeRecord],
    ) -> tuple[StaleWorktreeItem, _DirtyState] | None:
        """Classify a single child directory: None when active, else its orphaned item and dirty state."""
        record = records_by_id.get(child.name) or records_by_path.get(str(child.resolve()))
        if record is not None and record.status == WorktreeStatus.ACTIVE:
            return None

        return self._create_orphaned_item(child, record)

    def _detect_orphaned_directories(
        self, all_records: list[WorktreeRecord]
    ) -> tuple[list[StaleWorktreeItem], int, list[str], list[DiagnosticRecord]]:
        """Identify directories under the workspace's worktrees directory that are not active."""
        if not self.worktree_base_dir.exists():
            return [], 0, [], []

        records_by_id, records_by_path = _build_record_lookups(all_records)
        orphaned_items: list[StaleWorktreeItem] = []
        warnings: list[str] = []
        diagnostics: list[DiagnosticRecord] = []
        active_count = 0

        for child in sorted(self.worktree_base_dir.iterdir()):
            if not child.is_dir():
                continue
            orphaned = self._process_worktree_child(child, records_by_id, records_by_path)
            if orphaned is None:
                active_count += 1
                continue
            item, dirty = orphaned
            orphaned_items.append(item)
            if dirty.warning:
                warnings.append(dirty.warning)
            diagnostics.extend(dirty.diagnostics)

        return orphaned_items, active_count, warnings, diagnostics

    def _get_active_branch_names(
        self,
        worktree_entries: list[GitWorktreeEntry],
        all_records: list[WorktreeRecord],
    ) -> set[str]:
        """Collect branch names currently in use by active worktrees or active DB records."""
        active_git_worktrees = {e.branch for e in worktree_entries if e.branch and e.path.is_dir()}
        active_db = {
            r.branch_name for r in all_records if r.status == WorktreeStatus.ACTIVE and Path(r.worktree_path).is_dir()
        }
        return active_git_worktrees | active_db

    def _detect_stale_branches(
        self,
        worktree_entries: list[GitWorktreeEntry],
        all_records: list[WorktreeRecord],
    ) -> list[StaleWorktreeItem]:
        """Identify temporary worktree branches not checked out or attached to active worktrees."""
        try:
            worktree_branches = self.git.list_branches(self.path, pattern="dovo/dovo_*")
        except Exception as exc:
            if not isinstance(exc, GitNotFoundError):
                record_exception(
                    exc, level=DiagnosticLevel.DEBUG, operation="worktree.detect", context={"path": str(self.path)}
                )
            return []

        active_branches = self._get_active_branch_names(worktree_entries, all_records)
        return [
            StaleWorktreeItem(
                category=StaleWorktreeCategory.STALE_BRANCH,
                identifier=b,
                branch_name=b,
                reason=f"Worktree branch '{b}' is not attached to any active worktree",
            )
            for b in worktree_branches
            if b not in active_branches
        ]

    def detect(self) -> WorktreeDetection:
        """Scan and classify all stale worktree resources non-destructively.

        Raises:
            GitError: When git worktree list fails, with its own error_code and message.
        """
        worktree_entries = self.git.worktree_list(self.path)
        all_records = self.db.list()

        stale_worktrees = self._detect_stale_worktree_refs(worktree_entries)
        stale_db_records = self._detect_stale_db_records(all_records)
        orphaned_dirs, active_count, warnings, diagnostics = self._detect_orphaned_directories(all_records)
        stale_branches = self._detect_stale_branches(worktree_entries, all_records)

        all_items = [
            *stale_worktrees,
            *orphaned_dirs,
            *stale_db_records,
            *stale_branches,
        ]

        return WorktreeDetection(
            items=tuple(all_items),
            active_worktree_count=active_count,
            warnings=tuple(warnings),
            diagnostics=tuple(diagnostics),
        )


def detect_stale_worktrees(
    paths: WorkspacePaths,
    db: WorktreesRepository,
    *,
    git: GitRunner,
    sessions_db: SessionsRepository | None = None,
) -> WorktreeDetection:
    """Scan paths.worktrees_dir for stale worktree resources; raise GitError when git worktree list fails."""
    return WorktreeDetector(paths, db, git=git, sessions_db=sessions_db).detect()
