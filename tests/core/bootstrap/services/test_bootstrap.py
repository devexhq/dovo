"""Contract tests for dovo.core.bootstrap.services.bootstrap.bootstrap_dovo."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.constants import BOOTSTRAP_META_REL, DOVO_GITIGNORE_CONTENT
from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import Filesystem
from dovo.common.models import Remediation
from dovo.common.utils import display_path
from dovo.core.bootstrap.exceptions import BootstrapError
from dovo.core.bootstrap.models import BootstrapOutcome
from dovo.core.bootstrap.services.bootstrap import bootstrap_dovo

_RERUN_REMEDIATION = Remediation(message="Resolve the path conflict above, then rerun 'dovo init'")
_OS_ERROR = PermissionError(13, "Permission denied")


def _fresh(root_path: Path) -> None:
    return None


def _bootstrapped(root_path: Path) -> None:
    bootstrap_dovo(root_path)


def _missing_meta(root_path: Path) -> None:
    bootstrap_dovo(root_path)
    (root_path / BOOTSTRAP_META_REL).unlink()
    (root_path / ".meta").rmdir()


class BootstrapDovoTests:
    """Contract tests for bootstrap_dovo's summary, directory trimming and local .gitignore seeding."""

    @pytest.mark.parametrize(
        ("arrange", "outcome", "created", "existing"),
        [
            pytest.param(_fresh, BootstrapOutcome.INITIALIZED, (".meta",), (), id="fresh"),
            pytest.param(_bootstrapped, BootstrapOutcome.ALREADY_INITIALIZED, (), (".meta",), id="rerun"),
            pytest.param(_missing_meta, BootstrapOutcome.REPAIRED, (".meta",), (), id="missing-subdir"),
        ],
    )
    def test_returns_summary_with_outcome(
        self,
        tmp_path: Path,
        arrange: Callable[[Path], None],
        outcome: BootstrapOutcome,
        created: tuple[str, ...],
        existing: tuple[str, ...],
    ) -> None:
        root_path = tmp_path / ".dovo"
        arrange(root_path)

        summary = bootstrap_dovo(root_path)

        assert summary.outcome == outcome
        assert summary.dirs_created == tuple(root_path / name for name in created)
        assert summary.dirs_existing == tuple(root_path / name for name in existing)
        assert summary.repaired is (outcome == BootstrapOutcome.REPAIRED)

    def test_bootstrap_dovo_fresh_root_creates_gitignore_with_expected_content(self, tmp_path: Path) -> None:
        root_path = tmp_path / ".dovo"

        result = bootstrap_dovo(root_path)

        assert result.gitignore_created is True
        assert result.dirs_created == (result.root_path / ".meta",)
        assert (result.root_path / ".gitignore").read_text(encoding="utf-8") == DOVO_GITIGNORE_CONTENT

    def test_bootstrap_dovo_rerun_appends_missing_entries_and_keeps_existing_lines(self, tmp_path: Path) -> None:
        root_path = tmp_path / ".dovo"
        first_result = bootstrap_dovo(root_path)
        (first_result.root_path / ".gitignore").write_text("worktrees/\ncustom-entry\n", encoding="utf-8")

        result = bootstrap_dovo(root_path)

        assert result.gitignore_created is False
        assert (result.root_path / ".gitignore").read_text(encoding="utf-8") == (
            "worktrees/\ncustom-entry\n.meta/\n.lock\nrun\n*.db\n*.db-journal\n*.db-wal\n"
        )

    def test_bootstrap_dovo_rerun_terminates_last_line_before_appending(self, tmp_path: Path) -> None:
        root_path = tmp_path / ".dovo"
        first_result = bootstrap_dovo(root_path)
        complete_but_run = DOVO_GITIGNORE_CONTENT.replace("run\n", "").rstrip("\n")
        (first_result.root_path / ".gitignore").write_text(complete_but_run, encoding="utf-8")

        result = bootstrap_dovo(root_path)

        assert (result.root_path / ".gitignore").read_text(encoding="utf-8") == complete_but_run + "\nrun\n"

    def test_bootstrap_dovo_rerun_leaves_complete_gitignore_unchanged(self, tmp_path: Path) -> None:
        root_path = tmp_path / ".dovo"
        first_result = bootstrap_dovo(root_path)
        complete_content = "# local\n" + DOVO_GITIGNORE_CONTENT + "catalog/index.json\n"
        (first_result.root_path / ".gitignore").write_text(complete_content, encoding="utf-8")

        result = bootstrap_dovo(root_path)

        assert result.gitignore_created is False
        assert (result.root_path / ".gitignore").read_text(encoding="utf-8") == complete_content


def _root_is_file(root_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ErrorCode, str]:
    root_path.write_text("", encoding="utf-8")
    return ErrorCode.BOOTSTRAP_PATH_INVALID, f"{display_path(root_path)} exists but is a file, not a directory."


def _root_symlink_to_file(root_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ErrorCode, str]:
    target = root_path.parent / "elsewhere"
    target.write_text("", encoding="utf-8")
    root_path.symlink_to(target)
    return ErrorCode.BOOTSTRAP_PATH_INVALID, f"{display_path(target)} exists but is a file, not a directory."


def _subdir_is_symlink(root_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ErrorCode, str]:
    (root_path.parent / "meta-target").mkdir()
    root_path.mkdir()
    (root_path / ".meta").symlink_to(root_path.parent / "meta-target")
    return ErrorCode.BOOTSTRAP_PATH_INVALID, f"{display_path(root_path / '.meta')} must be a directory, not a symlink."


def _mkdir_fails(root_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ErrorCode, str]:
    def _raise(*_args: object, **_kwargs: object) -> None:
        raise _OS_ERROR

    monkeypatch.setattr(Path, "mkdir", _raise)
    return (
        ErrorCode.BOOTSTRAP_PATH_INVALID,
        f"Could not create {display_path(root_path)}: {_OS_ERROR}. Check directory permissions and try again.",
    )


def _root_unwritable(root_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ErrorCode, str]:
    root_path.mkdir()
    monkeypatch.setattr(os, "access", lambda *_args, **_kwargs: False)
    return (
        ErrorCode.BOOTSTRAP_PATH_UNWRITABLE,
        f"{display_path(root_path)} is not writable. Check directory permissions and try again.",
    )


def _metadata_write_fails(root_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ErrorCode, str]:
    def _raise(*_args: object, **_kwargs: object) -> None:
        raise _OS_ERROR

    monkeypatch.setattr(Filesystem, "atomic_write_json", staticmethod(_raise))
    meta_path = root_path / BOOTSTRAP_META_REL
    return (
        ErrorCode.BOOTSTRAP_METADATA_WRITE_FAILED,
        f"Could not write bootstrap metadata at {display_path(meta_path)}: {_OS_ERROR}",
    )


class BootstrapDovoFailureTests:
    @pytest.mark.parametrize(
        ("arrange", "chains_os_error"),
        [
            pytest.param(_root_is_file, False, id="root-is-file"),
            pytest.param(_root_symlink_to_file, False, id="root-symlink-to-file"),
            pytest.param(_subdir_is_symlink, False, id="subdir-is-symlink"),
            pytest.param(_mkdir_fails, True, id="mkdir-failed"),
            pytest.param(_root_unwritable, False, id="root-unwritable"),
            pytest.param(_metadata_write_fails, True, id="metadata-write-failed"),
        ],
    )
    def test_failure_raises_bootstrap_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        arrange: Callable[[Path, pytest.MonkeyPatch], tuple[ErrorCode, str]],
        chains_os_error: bool,
    ) -> None:
        """Raise BootstrapError with the case's code and message and the one rerun remediation."""
        root_path = tmp_path.resolve() / ".dovo"
        error_code, message = arrange(root_path, monkeypatch)

        with pytest.raises(BootstrapError) as raised:
            bootstrap_dovo(root_path)

        assert (raised.value.error_code, raised.value.message) == (error_code, message)
        assert raised.value.remediations == (_RERUN_REMEDIATION,)
        assert raised.value.__cause__ is (_OS_ERROR if chains_os_error else None)
