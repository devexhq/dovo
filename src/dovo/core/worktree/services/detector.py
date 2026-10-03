"""Stale worktree detection and resource inspection service."""

from __future__ import annotations

from pathlib import Path

from dovo.core.db import RunsRepository, WorktreeRecord, WorktreesRepository, WorktreeStatus
from dovo.core.git.exceptions import GitError
from dovo.core.git.models import GitWorktreeEntry
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import (
    StaleWorktreeCategory,
    StaleWorktreeItem,
    WorktreeDetectionResult,
    WorktreeDetectionStatus,
)


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
        path: Path,
        db: WorktreesRepository,
        runs_db: RunsRepository | None = None,
    ) -> None:
        """Initialize detector bound to repository root and database.

        Args:
            path: Repository root directory.
            db: WorktreesRepository instance.
            runs_db: Optional RunsRepository instance for run liveness checks.
        """
        self.path = path.expanduser().resolve()
        self.worktree_base_dir = self.path / ".dovo" / "worktrees"
        self.db = db
        self.runs_db = runs_db

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

    def _inspect_directory_dirty_state(self, dir_path: Path) -> tuple[bool, int, str | None]:
        """Check whether an orphaned directory contains uncommitted or untracked changes."""
        try:
            status_lines = GitRunner.status_porcelain(dir_path)
            if status_lines:
                return True, len(status_lines), None
            return False, 0, None
        except Exception as exc:
            return (
                False,
                0,
                f"Unable to inspect git status for orphaned directory '{dir_path.name}': {exc}",
            )

    def _create_orphaned_item(
        self,
        child: Path,
        record: WorktreeRecord | None,
    ) -> tuple[StaleWorktreeItem, str | None]:
        """Build StaleWorktreeItem and optional warning for an orphaned directory."""
        is_dirty, dirty_count, warn = self._inspect_directory_dirty_state(child)
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
            is_dirty=is_dirty,
            dirty_file_count=dirty_count,
            reason=reason,
        )
        return item, warn

    def _process_worktree_child(
        self,
        child: Path,
        records_by_id: dict[str, WorktreeRecord],
        records_by_path: dict[str, WorktreeRecord],
    ) -> tuple[bool, StaleWorktreeItem | None, str | None]:
        """Classify a single child directory as active or orphaned."""
        record = records_by_id.get(child.name) or records_by_path.get(str(child.resolve()))
        if record is not None and record.status == WorktreeStatus.ACTIVE:
            return True, None, None

        item, warn = self._create_orphaned_item(child, record)
        return False, item, warn

    def _detect_orphaned_directories(
        self, all_records: list[WorktreeRecord]
    ) -> tuple[list[StaleWorktreeItem], int, list[str]]:
        """Identify directories under `.dovo/worktrees` that are not active."""
        if not self.worktree_base_dir.exists():
            return [], 0, []

        records_by_id, records_by_path = _build_record_lookups(all_records)
        orphaned_items: list[StaleWorktreeItem] = []
        warnings: list[str] = []
        active_count = 0

        for child in sorted(self.worktree_base_dir.iterdir()):
            if not child.is_dir():
                continue
            is_active, item, warn = self._process_worktree_child(child, records_by_id, records_by_path)
            if is_active:
                active_count += 1
            elif item is not None:
                orphaned_items.append(item)
                if warn:
                    warnings.append(warn)

        return orphaned_items, active_count, warnings

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
            worktree_branches = GitRunner.list_branches(self.path, pattern="dovo/dovo_*")
        except Exception:
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

    def detect(self) -> WorktreeDetectionResult:
        """Scan and classify all stale worktree resources non-destructively.

        Returns:
            Structured WorktreeDetectionResult containing all detected items.
        """
        try:
            worktree_entries = GitRunner.worktree_list(self.path)
        except GitError as exc:
            return WorktreeDetectionResult(
                status=WorktreeDetectionStatus.GIT_FAILED,
                errors=[f"Failed to list git worktrees (GIT_FAILED): {exc}"],
            )
        except Exception as exc:
            return WorktreeDetectionResult(
                status=WorktreeDetectionStatus.ERROR,
                errors=[f"Unexpected error while listing git worktrees: {exc}"],
            )

        try:
            all_records = self.db.list()
        except Exception as exc:
            return WorktreeDetectionResult(
                status=WorktreeDetectionStatus.ERROR,
                errors=[f"Failed to query worktrees from database: {exc}"],
            )

        stale_worktrees = self._detect_stale_worktree_refs(worktree_entries)
        stale_db_records = self._detect_stale_db_records(all_records)
        orphaned_dirs, active_count, warnings = self._detect_orphaned_directories(all_records)
        stale_branches = self._detect_stale_branches(worktree_entries, all_records)

        all_items = [
            *stale_worktrees,
            *orphaned_dirs,
            *stale_db_records,
            *stale_branches,
        ]

        return WorktreeDetectionResult(
            status=WorktreeDetectionStatus.OK,
            items=all_items,
            active_worktree_count=active_count,
            warnings=warnings,
        )


def detect_stale_worktrees(
    path: Path,
    db: WorktreesRepository,
    runs_db: RunsRepository | None = None,
) -> WorktreeDetectionResult:
    """Non-raising helper to scan and detect stale worktree resources."""
    detector = WorktreeDetector(path, db, runs_db=runs_db)
    return detector.detect()
