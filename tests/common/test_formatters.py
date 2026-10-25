"""Tier 1 unit tests for the shared remediation rendering."""

from __future__ import annotations

import pytest

from dovo.common.formatters import format_remediations
from dovo.common.models import Remediation


class FormatRemediationsTests:
    """Unit tests pinning the one terminal rendering of remediations."""

    @pytest.mark.parametrize(
        ("remediations", "expected"),
        [
            pytest.param([], "", id="empty"),
            pytest.param(
                [Remediation(message="Check file permissions and that the path is readable")],
                "Fix:\n- Check file permissions and that the path is readable",
                id="one",
            ),
            pytest.param(
                [
                    Remediation(
                        message="Run 'dovo worktree list' to see known worktrees", command="dovo worktree list"
                    ),
                    Remediation(message="Pass '--allow-dirty' to overlay changes anyway"),
                ],
                "Fix:\n- Run 'dovo worktree list' to see known worktrees\n- Pass '--allow-dirty' to overlay changes anyway",
                id="many-command-not-printed",
            ),
        ],
    )
    def test_formats_fix_block(self, remediations: list[Remediation], expected: str) -> None:
        """Return '' for none, else 'Fix:' then one '- <message>' line each, never the command."""
        assert format_remediations(remediations) == expected
