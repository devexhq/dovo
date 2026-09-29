"""History domain entrypoint coordinator."""

from __future__ import annotations

from worktree.common.filesystem import WorkspacePaths
from worktree.core.db import RunsRepository, RunStatus
from worktree.core.engine.services.reconcile import reconcile_stale_runs
from worktree.core.history.models import (
    HistoryListResult,
    HistoryListStatus,
    HistoryShowResult,
    HistoryShowStatus,
)
from worktree.core.logs import RunLogEvent
from worktree.core.logs.services.read import list_session_log_files, read_run_log_events


def _render_run_log_line(event: RunLogEvent) -> str:
    """Render one run.log event as a short text line of its timestamp, kind, and populated scalar fields."""
    suffix = "".join(f" {name}={value}" for name, value in event.details().items())
    return f"[{event.ts}] {event.event.value}{suffix}"


class History:
    """Unified entrypoint for execution run history inspection and retrieval."""

    def __init__(self, paths: WorkspacePaths, db: RunsRepository | None = None) -> None:
        self.paths = paths
        self.path = paths.root_dir
        self.db = db if db is not None else RunsRepository(db_path=paths.database_file, project_id=paths.project_id)

    def list(
        self,
        *,
        limit: int | None = 20,
        status: str | None = None,
    ) -> HistoryListResult:
        """Retrieve filtered execution runs from database."""
        warnings: list[str] = []
        reconciliation_result = reconcile_stale_runs(self.db, path=self.path)
        if reconciliation_result.warning:
            warnings.append(reconciliation_result.warning)

        status_filter: RunStatus | str | None = None
        if status is not None:
            try:
                status_filter = RunStatus(status.lower())
            except ValueError:
                status_filter = status

        runs = self.db.list(limit=limit, status=status_filter)
        return HistoryListResult(status=HistoryListStatus.OK, runs=runs, warnings=warnings)

    def show(self, session_id: str, *, include_logs: bool = False) -> HistoryShowResult:
        """Look up execution session metadata and run details, plus log file paths and a run.log tail when requested."""
        row = self.db.get(session_id)
        if row is None:
            return HistoryShowResult(status=HistoryShowStatus.NOT_FOUND, session_id=session_id)

        if not include_logs:
            return HistoryShowResult(status=HistoryShowStatus.OK, session_id=session_id, run=row)

        session_log_dir = self.paths.logs_dir / session_id
        try:
            log_files = [str(p) for p in list_session_log_files(session_log_dir)]
            log_snippet = [_render_run_log_line(e) for e in read_run_log_events(session_log_dir, tail=10)]
        except OSError as exc:
            return HistoryShowResult(
                status=HistoryShowStatus.OK,
                session_id=session_id,
                run=row,
                errors=[f"Failed reading session logs in '{session_log_dir}': {exc}"],
            )

        return HistoryShowResult(
            status=HistoryShowStatus.OK, session_id=session_id, run=row, log_files=log_files, log_snippet=log_snippet
        )
