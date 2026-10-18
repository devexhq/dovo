"""Contract tests for the worktree storage bridge (.dovo/run symlink to the run's session directory)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dovo.common.constants import DOVO_GITIGNORE_CONTENT
from dovo.common.filesystem.models import WorkspacePaths
from dovo.engine import storage_bridge
from dovo.engine.storage_bridge import create_storage_bridge, remove_storage_bridge

SESSION_ID = "bridge-session"


def _bridge_path(worktree_path: Path) -> Path:
    return worktree_path / ".dovo" / "run"


class CreateStorageBridgeTests:
    def test_create_links_run_to_missing_session_directory_and_creates_it(
        self, engine_paths: WorkspacePaths, tmp_path: Path
    ) -> None:
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        warnings: list[str] = []

        message = create_storage_bridge(engine_paths, SESSION_ID, worktree_path, warnings)

        assert message is None
        assert warnings == []
        assert _bridge_path(worktree_path).is_symlink()
        assert _bridge_path(worktree_path).resolve() == engine_paths.session_dir(SESSION_ID).resolve()
        assert engine_paths.session_dir(SESSION_ID).is_dir()

    def test_bridge_is_ignored_by_seeded_gitignore(self, engine_paths: WorkspacePaths, git_repo: Path) -> None:
        (git_repo / ".dovo").mkdir()
        (git_repo / ".dovo" / ".gitignore").write_text(DOVO_GITIGNORE_CONTENT, encoding="utf-8")
        subprocess.run(["git", "add", ".dovo/.gitignore"], cwd=git_repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "seed"], cwd=git_repo, check=True, capture_output=True)

        message = create_storage_bridge(engine_paths, SESSION_ID, git_repo, [])

        status = subprocess.run(
            ["git", "status", "--porcelain", "--", ".dovo/run"],
            cwd=git_repo,
            check=True,
            capture_output=True,
            text=True,
        )
        assert message is None
        assert _bridge_path(git_repo).is_symlink()
        assert status.stdout == ""

    def test_create_with_symlink_to_other_directory_replaces_link_and_preserves_old_target(
        self, engine_paths: WorkspacePaths, tmp_path: Path
    ) -> None:
        worktree_path = tmp_path / "worktree"
        old_target = tmp_path / "old-target"
        old_target.mkdir()
        sentinel_path = old_target / "sentinel.txt"
        sentinel_path.write_text("preserve me", encoding="utf-8")
        _bridge_path(worktree_path).parent.mkdir(parents=True)
        _bridge_path(worktree_path).symlink_to(old_target, target_is_directory=True)

        message = create_storage_bridge(engine_paths, SESSION_ID, worktree_path, [])

        assert message is None
        assert _bridge_path(worktree_path).resolve() == engine_paths.session_dir(SESSION_ID).resolve()
        assert sentinel_path.read_text(encoding="utf-8") == "preserve me"

    def test_create_with_regular_directory_returns_not_a_symlink_message_and_preserves_content(
        self, engine_paths: WorkspacePaths, tmp_path: Path
    ) -> None:
        worktree_path = tmp_path / "worktree"
        bridge_path = _bridge_path(worktree_path)
        bridge_path.mkdir(parents=True)
        sentinel_path = bridge_path / "sentinel.txt"
        sentinel_path.write_text("do not delete", encoding="utf-8")
        session_file = engine_paths.session_dir(SESSION_ID) / "session.json"
        session_file.parent.mkdir(parents=True)
        session_file.write_text("preserve me too", encoding="utf-8")
        warnings: list[str] = []

        message = create_storage_bridge(engine_paths, SESSION_ID, worktree_path, warnings)

        assert message == f"Worktree storage bridge path '{bridge_path}' is not a symlink."
        assert warnings == []
        assert not bridge_path.is_symlink()
        assert sentinel_path.read_text(encoding="utf-8") == "do not delete"
        assert session_file.read_text(encoding="utf-8") == "preserve me too"

    @pytest.mark.parametrize(
        ("fault", "message_prefix"),
        [
            pytest.param(
                "parent-blocked", "Unable to prepare worktree storage bridge at '{bridge}': ", id="parent-blocked"
            ),
            pytest.param(
                "unlink-fails", "Unable to replace worktree storage bridge at '{bridge}': ", id="unlink-fails"
            ),
            pytest.param(
                "symlink-fails", "Unable to create worktree storage bridge at '{bridge}': ", id="symlink-fails"
            ),
        ],
    )
    def test_create_failure_returns_classified_message(
        self,
        engine_paths: WorkspacePaths,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fault: str,
        message_prefix: str,
    ) -> None:
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        bridge_path = _bridge_path(worktree_path)
        if fault == "parent-blocked":
            (worktree_path / ".dovo").write_text("not a directory", encoding="utf-8")
        elif fault == "unlink-fails":
            bridge_path.parent.mkdir()
            bridge_path.symlink_to(tmp_path, target_is_directory=True)

            def _unlink_fails(self: Path, missing_ok: bool = False) -> None:
                raise OSError("busy")

            monkeypatch.setattr(Path, "unlink", _unlink_fails)
        else:

            def _symlink_fails(self: Path, target: Path, target_is_directory: bool = False) -> None:
                raise OSError("storage device I/O failure")

            monkeypatch.setattr(Path, "symlink_to", _symlink_fails)
        warnings: list[str] = []

        message = create_storage_bridge(engine_paths, SESSION_ID, worktree_path, warnings)

        assert message is not None
        assert message.startswith(message_prefix.format(bridge=bridge_path))
        assert warnings == []

    @pytest.mark.parametrize(
        ("system", "error_code", "is_warning"),
        [
            pytest.param("Windows", 1314, True, id="windows-1314"),
            pytest.param("Windows", 50, True, id="windows-50"),
            pytest.param("Linux", 1314, False, id="linux-1314"),
        ],
    )
    def test_create_symlink_privilege_failure_is_warning_only_on_windows(
        self,
        engine_paths: WorkspacePaths,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        system: str,
        error_code: int,
        is_warning: bool,
    ) -> None:
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        bridge_path = _bridge_path(worktree_path)
        error = OSError(error_code, "symlink privilege unavailable")

        def _symlink_fails(self: Path, target: Path, target_is_directory: bool = False) -> None:
            raise error

        monkeypatch.setattr(storage_bridge.platform, "system", lambda: system)
        monkeypatch.setattr(Path, "symlink_to", _symlink_fails)
        warnings: list[str] = []

        message = create_storage_bridge(engine_paths, SESSION_ID, worktree_path, warnings)

        expected = f"Unable to create worktree storage bridge at '{bridge_path}': {error}"
        assert (message, warnings) == ((None, [expected]) if is_warning else (expected, []))
        assert not bridge_path.is_symlink()


class RemoveStorageBridgeTests:
    def test_remove_unlinks_symlink_and_preserves_target_contents(self, tmp_path: Path) -> None:
        worktree_path = tmp_path / "worktree"
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        sentinel_path = target_dir / "sentinel.txt"
        sentinel_path.write_text("preserve me", encoding="utf-8")
        _bridge_path(worktree_path).parent.mkdir(parents=True)
        _bridge_path(worktree_path).symlink_to(target_dir, target_is_directory=True)

        message = remove_storage_bridge(worktree_path)

        assert message is None
        assert not _bridge_path(worktree_path).is_symlink()
        assert sentinel_path.read_text(encoding="utf-8") == "preserve me"

    @pytest.mark.parametrize(
        "existing",
        [pytest.param("absent", id="absent"), pytest.param("regular-directory", id="regular-directory")],
    )
    def test_remove_without_symlink_returns_none_and_leaves_path_untouched(self, tmp_path: Path, existing: str) -> None:
        worktree_path = tmp_path / "worktree"
        bridge_path = _bridge_path(worktree_path)
        sentinel_path = bridge_path / "sentinel.txt"
        if existing == "regular-directory":
            bridge_path.mkdir(parents=True)
            sentinel_path.write_text("do not delete", encoding="utf-8")

        message = remove_storage_bridge(worktree_path)

        assert message is None
        assert bridge_path.exists() == (existing == "regular-directory")
        if existing == "regular-directory":
            assert sentinel_path.read_text(encoding="utf-8") == "do not delete"

    def test_remove_unlink_failure_returns_failed_to_unlink_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        worktree_path = tmp_path / "worktree"
        bridge_path = _bridge_path(worktree_path)
        bridge_path.parent.mkdir(parents=True)
        bridge_path.symlink_to(tmp_path, target_is_directory=True)

        def _unlink_fails(self: Path, missing_ok: bool = False) -> None:
            raise OSError("busy")

        monkeypatch.setattr(Path, "unlink", _unlink_fails)

        message = remove_storage_bridge(worktree_path)

        assert message == f"Failed to unlink worktree storage bridge at '{bridge_path}': busy"
