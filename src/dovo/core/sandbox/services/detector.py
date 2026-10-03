"""Stale sandbox detection and resource inspection service."""

from __future__ import annotations

from pathlib import Path

from dovo.core.db import RunsRepository, SandboxesRepository, SandboxRecord, SandboxStatus
from dovo.core.git.exceptions import GitError
from dovo.core.git.models import GitWorktreeEntry
from dovo.core.git.runner import GitRunner
from dovo.core.sandbox.models import (
    SandboxDetectionResult,
    SandboxDetectionStatus,
    StaleSandboxCategory,
    StaleSandboxItem,
)


def _build_record_lookups(
    all_records: list[SandboxRecord],
) -> tuple[dict[str, SandboxRecord], dict[str, SandboxRecord]]:
    """Index sandbox records by ID and resolved string path."""
    by_id: dict[str, SandboxRecord] = {}
    by_path: dict[str, SandboxRecord] = {}
    for r in all_records:
        by_id[r.id] = r
        by_path[str(Path(r.sandbox_path).resolve())] = r
    return by_id, by_path


class SandboxDetector:
    """Non-destructive scanner for stale worktrees, orphaned directories, and dead records."""

    def __init__(
        self,
        path: Path,
        db: SandboxesRepository,
        runs_db: RunsRepository | None = None,
    ) -> None:
        """Initialize detector bound to repository root and database.

        Args:
            path: Repository root directory.
            db: SandboxesRepository instance.
            runs_db: Optional RunsRepository instance for run liveness checks.
        """
        self.path = path.expanduser().resolve()
        self.sandbox_base_dir = self.path / ".dovo" / "sandboxes"
        self.db = db
        self.runs_db = runs_db

    def _detect_stale_worktree_refs(self, worktree_entries: list[GitWorktreeEntry]) -> list[StaleSandboxItem]:
        """Identify registered git worktree refs pointing to missing paths or prunable."""
        stale_items: list[StaleSandboxItem] = []
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
                    StaleSandboxItem(
                        category=StaleSandboxCategory.STALE_WORKTREE_REF,
                        identifier=str(entry.path),
                        path=entry.path,
                        branch_name=entry.branch,
                        reason=reason,
                    )
                )
        return stale_items

    def _detect_stale_db_records(self, all_records: list[SandboxRecord]) -> list[StaleSandboxItem]:
        """Identify active sandbox database rows whose directory is missing on disk."""
        stale_items: list[StaleSandboxItem] = []
        for record in all_records:
            if record.status == SandboxStatus.ACTIVE:
                sandbox_path = Path(record.sandbox_path)
                if not sandbox_path.is_dir():
                    stale_items.append(
                        StaleSandboxItem(
                            category=StaleSandboxCategory.STALE_DB_RECORD,
                            identifier=record.id,
                            session_id=record.id,
                            path=sandbox_path,
                            branch_name=record.branch_name,
                            reason=f"Active database record '{record.id}' has missing sandbox path on disk",
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
        record: SandboxRecord | None,
    ) -> tuple[StaleSandboxItem, str | None]:
        """Build StaleSandboxItem and optional warning for an orphaned directory."""
        is_dirty, dirty_count, warn = self._inspect_directory_dirty_state(child)
        if record is None:
            reason = f"Sandbox directory '{child.name}' is not tracked in the database"
        else:
            reason = f"Sandbox directory '{child.name}' has database status '{record.status.value}'"

        item = StaleSandboxItem(
            category=StaleSandboxCategory.ORPHANED_DIRECTORY,
            identifier=child.name,
            session_id=record.id if record else None,
            path=child,
            branch_name=record.branch_name if record else None,
            is_dirty=is_dirty,
            dirty_file_count=dirty_count,
            reason=reason,
        )
        return item, warn

    def _process_sandbox_child(
        self,
        child: Path,
        records_by_id: dict[str, SandboxRecord],
        records_by_path: dict[str, SandboxRecord],
    ) -> tuple[bool, StaleSandboxItem | None, str | None]:
        """Classify a single child directory as active or orphaned."""
        record = records_by_id.get(child.name) or records_by_path.get(str(child.resolve()))
        if record is not None and record.status == SandboxStatus.ACTIVE:
            return True, None, None

        item, warn = self._create_orphaned_item(child, record)
        return False, item, warn

    def _detect_orphaned_directories(
        self, all_records: list[SandboxRecord]
    ) -> tuple[list[StaleSandboxItem], int, list[str]]:
        """Identify directories under `.dovo/sandboxes` that are not active."""
        if not self.sandbox_base_dir.exists():
            return [], 0, []

        records_by_id, records_by_path = _build_record_lookups(all_records)
        orphaned_items: list[StaleSandboxItem] = []
        warnings: list[str] = []
        active_count = 0

        for child in sorted(self.sandbox_base_dir.iterdir()):
            if not child.is_dir():
                continue
            is_active, item, warn = self._process_sandbox_child(child, records_by_id, records_by_path)
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
        all_records: list[SandboxRecord],
    ) -> set[str]:
        """Collect branch names currently in use by active worktrees or active DB records."""
        active_wt = {e.branch for e in worktree_entries if e.branch and e.path.is_dir()}
        active_db = {
            r.branch_name for r in all_records if r.status == SandboxStatus.ACTIVE and Path(r.sandbox_path).is_dir()
        }
        return active_wt | active_db

    def _detect_stale_branches(
        self,
        worktree_entries: list[GitWorktreeEntry],
        all_records: list[SandboxRecord],
    ) -> list[StaleSandboxItem]:
        """Identify temporary sandbox branches not checked out or attached to active sandboxes."""
        try:
            sandbox_branches = GitRunner.list_branches(self.path, pattern="worktree/sandbox-*")
        except Exception:
            return []

        active_branches = self._get_active_branch_names(worktree_entries, all_records)
        return [
            StaleSandboxItem(
                category=StaleSandboxCategory.STALE_BRANCH,
                identifier=b,
                branch_name=b,
                reason=f"Sandbox branch '{b}' is not attached to any active sandbox or worktree",
            )
            for b in sandbox_branches
            if b not in active_branches
        ]

    def detect(self) -> SandboxDetectionResult:
        """Scan and classify all stale sandbox resources non-destructively.

        Returns:
            Structured SandboxDetectionResult containing all detected items.
        """
        try:
            worktree_entries = GitRunner.worktree_list(self.path)
        except GitError as exc:
            return SandboxDetectionResult(
                status=SandboxDetectionStatus.GIT_FAILED,
                errors=[f"Failed to list git worktrees (GIT_FAILED): {exc}"],
            )
        except Exception as exc:
            return SandboxDetectionResult(
                status=SandboxDetectionStatus.ERROR,
                errors=[f"Unexpected error while listing git worktrees: {exc}"],
            )

        try:
            all_records = self.db.list()
        except Exception as exc:
            return SandboxDetectionResult(
                status=SandboxDetectionStatus.ERROR,
                errors=[f"Failed to query sandboxes from database: {exc}"],
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

        return SandboxDetectionResult(
            status=SandboxDetectionStatus.OK,
            items=all_items,
            active_sandbox_count=active_count,
            warnings=warnings,
        )


def detect_stale_sandboxes(
    path: Path,
    db: SandboxesRepository,
    runs_db: RunsRepository | None = None,
) -> SandboxDetectionResult:
    """Non-raising helper to scan and detect stale sandbox resources."""
    detector = SandboxDetector(path, db, runs_db=runs_db)
    return detector.detect()
