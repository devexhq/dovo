"""Contract tests for the Diff facade: constructor wiring and pass-through delegation."""

from __future__ import annotations

from pathlib import Path

from worktree.core.diff.facade import Diff
from worktree.core.diff.models import DiffStatus
from worktree.core.diff.writer import get_session_dir, write_session_diff


class DiffInspectDelegationTests:
    def test_inspect_returns_ok_for_a_session_with_a_persisted_patch(self, tmp_path: Path) -> None:
        """[tier-1/integration] Diff.inspect: a session with a written diff.patch is returned with OK status and the exact diff text."""
        patch_text = "diff --git a/f.txt b/f.txt\n-old\n+new\n"
        write_session_diff(get_session_dir(tmp_path, "session-1"), patch_text)

        result = Diff(tmp_path).inspect(session_id="session-1")

        assert result.status == DiffStatus.OK
        assert result.diff_text == patch_text

    def test_inspect_passes_raw_full_and_max_lines_through_to_the_result(self, tmp_path: Path) -> None:
        """[tier-1/integration] Diff.inspect: constructor's raw/full/max_lines flags are forwarded into the returned DiffResult."""
        write_session_diff(get_session_dir(tmp_path, "session-2"), "diff --git a/f.txt b/f.txt\n")

        result = Diff(tmp_path, raw=True, full=True, max_lines=10).inspect(session_id="session-2")

        assert result.raw is True
        assert result.full is True
        assert result.max_lines == 10

    def test_inspect_with_no_session_id_discovers_the_latest_session(self, tmp_path: Path) -> None:
        """[tier-1/integration] Diff.inspect: session_id omitted -> discovers and returns the most recently modified session's diff."""
        write_session_diff(get_session_dir(tmp_path, "older"), "diff --git a/old.txt b/old.txt\n")
        write_session_diff(get_session_dir(tmp_path, "newer"), "diff --git a/new.txt b/new.txt\n")

        result = Diff(tmp_path).inspect()

        assert result.status == DiffStatus.OK
        assert result.session_id == "newer"


class DiffStaticHelperDelegationTests:
    def test_session_dir_matches_get_session_dir(self, tmp_path: Path) -> None:
        """[tier-1/unit] Diff.session_dir: static helper returns the identical path as calling get_session_dir directly."""
        assert Diff.session_dir(tmp_path, "session-3") == get_session_dir(tmp_path, "session-3")

    def test_write_persists_diff_text_and_returns_the_patch_file_path(self, tmp_path: Path) -> None:
        """[tier-1/integration] Diff.write: static helper writes diff.patch under the given session directory and returns its path."""
        session_dir = tmp_path / "session-dir"
        session_dir.mkdir()

        target = Diff.write(session_dir, "diff content")

        assert target == session_dir / "diff.patch"
        assert target.read_text(encoding="utf-8") == "diff content"
