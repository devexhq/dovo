"""Contract tests for per-attempt scratch and control directory allocation."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.core.agents import AgentScratchResult, allocate_invocation_paths, new_invocation_id
from dovo.core.agents.scratch import scratch_unavailable_message

_INVOCATION_ID = "0123456789abcdef0123456789abcdef"
_FIX = "restore access to the session temp directory or correct its storage path."


def _layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Return (session_tmp_dir, worktree, main_checkout), all existing siblings."""
    session_tmp = tmp_path / "session-tmp"
    worktree = tmp_path / "worktree"
    main_checkout = tmp_path / "main"
    for directory in (session_tmp, worktree, main_checkout):
        directory.mkdir()

    return session_tmp, worktree, main_checkout


class AllocateInvocationPathsTests:
    def test_valid_session_dir_creates_scratch_and_control_under_the_pre_determined_layout(
        self, tmp_path: Path
    ) -> None:
        """[tier-1/unit] allocate_invocation_paths: a valid session_tmp_dir returns ok=True with context.scratch_path == <root>/steps/<step>/agent/<id>/scratch and control_path == .../control, both existing real directories, and invocation_id preserved."""
        session_tmp, worktree, main_checkout = _layout(tmp_path)

        result = allocate_invocation_paths(
            invocation_id=_INVOCATION_ID,
            session_tmp_dir=session_tmp,
            step_id="build",
            worktree_path=worktree,
            main_checkout=main_checkout,
        )

        invocation_root = session_tmp.resolve() / "steps" / "build" / "agent" / _INVOCATION_ID
        assert result.ok
        assert result.invocation_id == _INVOCATION_ID
        assert result.context is not None
        assert result.context.invocation_id == _INVOCATION_ID
        assert result.context.scratch_path == invocation_root / "scratch"
        assert result.context.control_path == invocation_root / "control"
        assert result.context.scratch_path.is_dir()
        assert result.context.control_path.is_dir()

    def test_new_invocation_id_is_32_lowercase_hex_and_unique(self) -> None:
        """[tier-1/unit] new_invocation_id: two calls return distinct strings that each fully match ^[0-9a-f]{32}$."""
        first, second = new_invocation_id(), new_invocation_id()

        assert first != second
        assert re.fullmatch(r"[0-9a-f]{32}", first)
        assert re.fullmatch(r"[0-9a-f]{32}", second)

    @pytest.mark.parametrize(
        "step_id",
        [
            pytest.param("", id="empty"),
            pytest.param(".", id="dot"),
            pytest.param("..", id="dotdot"),
            pytest.param("../x", id="traversal"),
            pytest.param("a/b", id="slash"),
            pytest.param("a\\b", id="backslash"),
            pytest.param("a\x00b", id="nul"),
        ],
    )
    def test_unsafe_step_ids_fail_without_creating_anything(self, tmp_path: Path, step_id: str) -> None:
        """[tier-1/unit] allocate_invocation_paths: each unsafe step id returns ok=False with the invocation_id retained, errors[0] starting 'Cannot prepare private scratch for agent step', and no entry created under session_tmp_dir."""
        session_tmp, worktree, main_checkout = _layout(tmp_path)

        result = allocate_invocation_paths(
            invocation_id=_INVOCATION_ID,
            session_tmp_dir=session_tmp,
            step_id=step_id,
            worktree_path=worktree,
            main_checkout=main_checkout,
        )

        assert not result.ok
        assert result.invocation_id == _INVOCATION_ID
        assert result.errors[0].startswith("Cannot prepare private scratch for agent step")
        assert list(session_tmp.iterdir()) == []
        assert sorted(tmp_path.iterdir()) == sorted([session_tmp, worktree, main_checkout])

    def test_invalid_invocation_id_fails_instead_of_raising(self, tmp_path: Path) -> None:
        """[tier-1/unit] allocate_invocation_paths: an id that is not 32 lowercase hex returns ok=False with the supplied id and creates nothing."""
        session_tmp, worktree, main_checkout = _layout(tmp_path)

        result = allocate_invocation_paths(
            invocation_id="../escape",
            session_tmp_dir=session_tmp,
            step_id="build",
            worktree_path=worktree,
            main_checkout=main_checkout,
        )

        assert not result.ok
        assert result.invocation_id == "../escape"
        assert list(session_tmp.iterdir()) == []

    def test_missing_session_tmp_dir_fails_with_exact_diagnostic_and_separate_fix(self, tmp_path: Path) -> None:
        """[tier-1/unit] allocate_invocation_paths: session_tmp_dir None returns ok=False, error_code 'AGENT_SCRATCH_UNAVAILABLE', and errors == [scratch_unavailable_message(step_id, detail)] and fixes == ['restore access to the session temp directory or correct its storage path.']."""
        _, worktree, main_checkout = _layout(tmp_path)

        result = allocate_invocation_paths(
            invocation_id=_INVOCATION_ID,
            session_tmp_dir=None,
            step_id="build",
            worktree_path=worktree,
            main_checkout=main_checkout,
        )

        assert not result.ok
        assert result.error_code == "AGENT_SCRATCH_UNAVAILABLE"
        assert result.errors == [scratch_unavailable_message("build", "the session temp directory is unavailable")]
        assert result.errors == [
            "Cannot prepare private scratch for agent step 'build' (AGENT_SCRATCH_UNAVAILABLE): "
            "the session temp directory is unavailable"
        ]
        assert result.fixes == [_FIX]

    def test_nonexistent_session_tmp_dir_is_not_created(self, tmp_path: Path) -> None:
        """[tier-1/unit] allocate_invocation_paths: a session_tmp_dir that does not exist returns ok=False and is not created."""
        _, worktree, main_checkout = _layout(tmp_path)
        missing = tmp_path / "never-prepared"

        result = allocate_invocation_paths(
            invocation_id=_INVOCATION_ID,
            session_tmp_dir=missing,
            step_id="build",
            worktree_path=worktree,
            main_checkout=main_checkout,
        )

        assert not result.ok
        assert not missing.exists()

    def test_symlinked_step_directory_is_rejected_and_nothing_is_written_through_it(self, tmp_path: Path) -> None:
        """[tier-1/unit] allocate_invocation_paths: steps/<step> being a symlink to a directory outside the session root returns ok=False and the target directory stays empty."""
        session_tmp, worktree, main_checkout = _layout(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        (session_tmp / "steps").mkdir()
        (session_tmp / "steps" / "build").symlink_to(outside, target_is_directory=True)

        result = allocate_invocation_paths(
            invocation_id=_INVOCATION_ID,
            session_tmp_dir=session_tmp,
            step_id="build",
            worktree_path=worktree,
            main_checkout=main_checkout,
        )

        assert not result.ok
        assert result.error_code == "AGENT_SCRATCH_UNAVAILABLE"
        assert list(outside.iterdir()) == []

    @pytest.mark.parametrize(
        "place",
        [
            pytest.param(lambda session, worktree, main: (worktree / "tmp", worktree, main), id="inside-worktree"),
            pytest.param(lambda session, worktree, main: (main / "tmp", worktree, main), id="inside-main-checkout"),
            pytest.param(
                lambda session, worktree, main: (
                    session,
                    session / "steps" / "build" / "agent" / _INVOCATION_ID / "checkout",
                    main,
                ),
                id="contains-worktree",
            ),
            pytest.param(
                lambda session, worktree, main: (session, session / "steps" / "build" / "agent" / _INVOCATION_ID, main),
                id="equals-worktree",
            ),
        ],
    )
    def test_session_roots_overlapping_the_checkouts_are_rejected(
        self, tmp_path: Path, place: Callable[[Path, Path, Path], tuple[Path, Path, Path]]
    ) -> None:
        """[tier-1/unit] allocate_invocation_paths: each overlap layout returns ok=False and creates no invocation directory."""
        session_tmp, worktree, main_checkout = place(*_layout(tmp_path))
        session_tmp.mkdir(parents=True, exist_ok=True)

        result = allocate_invocation_paths(
            invocation_id=_INVOCATION_ID,
            session_tmp_dir=session_tmp,
            step_id="build",
            worktree_path=worktree,
            main_checkout=main_checkout,
        )

        assert not result.ok
        assert result.error_code == "AGENT_SCRATCH_UNAVAILABLE"
        assert not (session_tmp / "steps").exists()

    def test_existing_invocation_directory_is_never_reused(self, tmp_path: Path) -> None:
        """[tier-1/unit] allocate_invocation_paths: a second call with the same invocation_id, step id, and root returns ok=False and leaves the first call's files untouched."""
        session_tmp, worktree, main_checkout = _layout(tmp_path)

        def _allocate() -> AgentScratchResult:
            return allocate_invocation_paths(
                invocation_id=_INVOCATION_ID,
                session_tmp_dir=session_tmp,
                step_id="build",
                worktree_path=worktree,
                main_checkout=main_checkout,
            )

        first = _allocate()
        assert first.context is not None
        marker = first.context.scratch_path / "keep.txt"
        marker.write_text("first", encoding="utf-8")

        second = _allocate()

        assert not second.ok
        assert second.invocation_id == _INVOCATION_ID
        assert marker.read_text(encoding="utf-8") == "first"

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permission bits")
    def test_unwritable_session_root_returns_diagnostic_with_retained_id(self, tmp_path: Path) -> None:
        """[tier-1/unit] allocate_invocation_paths: a read-only session_tmp_dir returns ok=False, the supplied invocation_id on the result, and errors[0] containing the OSError detail."""
        session_tmp, worktree, main_checkout = _layout(tmp_path)
        session_tmp.chmod(0o500)

        try:
            result = allocate_invocation_paths(
                invocation_id=_INVOCATION_ID,
                session_tmp_dir=session_tmp,
                step_id="build",
                worktree_path=worktree,
                main_checkout=main_checkout,
            )
        finally:
            session_tmp.chmod(0o700)

        assert not result.ok
        assert result.invocation_id == _INVOCATION_ID
        assert "Permission denied" in result.errors[0]
