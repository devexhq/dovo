"""Semantic view models for run formatters."""

from __future__ import annotations

from dovo.cli.ui.formatters.base import ResultView
from dovo.core.db import SessionStatus


class RunResultView(ResultView):
    """Semantic view of a run or resume result: the shared header first, then the session it ran."""

    session_id: str | None = None
    blueprint_name: str | None = None
    session_status: SessionStatus | None = None
    paused_prompt_id: str | None = None
