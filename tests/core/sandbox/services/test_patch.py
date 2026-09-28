"""Contract tests for sandbox conflict extraction, diff inspection, and patch application."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.harness import WorkspaceBuilder
from worktree.core.db import SandboxesRepository
from worktree.core.db.models import SandboxStatus
from worktree.core.git.runner import GitRunner
from worktree.core.sandbox.models import SandboxApplyStatus, SandboxApplyStrategy, SandboxDiffStatus
from worktree.core.sandbox.services.lifecycle import SandboxLifecycle
from worktree.core.sandbox.services.patch import SandboxPatch, extract_conflicts


class ExtractConflictsTests:
    @pytest.mark.parametrize(
        ("stderr", "expected"),
        [
            pytest.param("error: patch failed: src/main.py:12\n", ["src/main.py"], id="patch_failed"),
            pytest.param("error: src/main.py: patch does not apply\n", ["src/main.py"], id="does_not_apply"),
            pytest.param("cannot apply binary patch to 'assets/logo.png'\n", ["assets/logo.png"], id="binary_patch"),
            pytest.param(
                "error: src/new.py: already exists in working directory\n", ["src/new.py"], id="already_exists"
            ),
            pytest.param("error: src/gone.py: does not exist in index\n", ["src/gone.py"], id="not_in_index"),
            pytest.param("Applying patch\nnothing to see here\n", [], id="no_conflict_markers"),
        ],
    )
    def test_extracts_conflicting_paths_from_known_git_apply_stderr_patterns(
        self, stderr: str, expected: list[str]
    ) -> None:
        """[tier-1/unit] extract_conflicts: recognizes each documented git apply failure line format and extracts its file path."""
        assert extract_conflicts(stderr) == expected

    def test_deduplicates_and_sorts_multiple_conflicting_paths(self) -> None:
        """[tier-1/unit] extract_conflicts: repeated and out-of-order conflict lines collapse into a sorted, deduplicated list."""
        stderr = "error: patch failed: zeta.py\nerror: patch failed: alpha.py\nerror: patch failed: zeta.py\n"
        assert extract_conflicts(stderr) == ["alpha.py", "zeta.py"]


@pytest.fixture
def sandbox_workspace(tmp_path: Path) -> Path:
    """Create an initialized Git workspace with a sandbox database."""
    return WorkspaceBuilder(tmp_path / "sandbox_ws").with_git().with_database().build()


def _create_sandbox_with_change(workspace: Path, sandbox_id: str) -> None:
    """Create a real sandbox and commit one file change inside it."""
    lifecycle = SandboxLifecycle(workspace, SandboxesRepository(workspace))
    result = lifecycle.create(session_id=sandbox_id)
    assert result.session is not None
    sandbox_path = result.session.sandbox_path
    (sandbox_path / "new_file.txt").write_text("sandbox change\n", encoding="utf-8")
    GitRunner.run(["add", "-A"], path=sandbox_path)
    GitRunner.run(["commit", "-m", "sandbox change"], path=sandbox_path)


class SandboxPatchDiffTests:
    def test_unknown_sandbox_id_returns_not_found(self, sandbox_workspace: Path) -> None:
        """[tier-1/integration] SandboxPatch.diff: no matching DB record -> NOT_FOUND with the sandbox id in errors."""
        patch = SandboxPatch(sandbox_workspace, SandboxesRepository(sandbox_workspace))

        result = patch.diff("missing_sandbox")

        assert result.status == SandboxDiffStatus.NOT_FOUND
        assert "missing_sandbox" in result.errors[0]

    def test_sandbox_directory_missing_on_disk_returns_not_found(self, sandbox_workspace: Path) -> None:
        """[tier-1/integration] SandboxPatch.diff: DB record exists but its sandbox_path directory was deleted -> NOT_FOUND."""
        db = SandboxesRepository(sandbox_workspace)
        _create_sandbox_with_change(sandbox_workspace, "sbx_gone")
        record = db.get("sbx_gone")
        assert record is not None
        import shutil

        shutil.rmtree(record.sandbox_path)
        patch = SandboxPatch(sandbox_workspace, db)

        result = patch.diff("sbx_gone")

        assert result.status == SandboxDiffStatus.NOT_FOUND

    def test_sandbox_with_no_changes_returns_empty_diff(self, sandbox_workspace: Path) -> None:
        """[tier-1/integration] SandboxPatch.diff: sandbox created with no further commits -> EMPTY_DIFF."""
        db = SandboxesRepository(sandbox_workspace)
        lifecycle = SandboxLifecycle(sandbox_workspace, db)
        create_result = lifecycle.create(session_id="sbx_empty")
        assert create_result.session is not None
        patch = SandboxPatch(sandbox_workspace, db)

        result = patch.diff("sbx_empty")

        assert result.status == SandboxDiffStatus.EMPTY_DIFF

    def test_sandbox_with_committed_change_returns_ok_with_touched_files(self, sandbox_workspace: Path) -> None:
        """[tier-1/integration] SandboxPatch.diff: sandbox with one committed file change -> OK, files_changed lists it, diff_text is non-empty."""
        db = SandboxesRepository(sandbox_workspace)
        _create_sandbox_with_change(sandbox_workspace, "sbx_diffme")
        patch = SandboxPatch(sandbox_workspace, db)

        result = patch.diff("sbx_diffme")

        assert result.status == SandboxDiffStatus.OK
        assert "new_file.txt" in result.files_changed
        assert result.diff_text.strip() != ""


class SandboxPatchApplyTests:
    def test_unknown_sandbox_id_returns_not_found(self, sandbox_workspace: Path) -> None:
        """[tier-1/integration] SandboxPatch.apply: no matching DB record -> NOT_FOUND."""
        patch = SandboxPatch(sandbox_workspace, SandboxesRepository(sandbox_workspace))

        result = patch.apply("missing_sandbox")

        assert result.status == SandboxApplyStatus.NOT_FOUND

    def test_already_merged_sandbox_returns_already_merged_without_reapplying(self, sandbox_workspace: Path) -> None:
        """[tier-1/integration] SandboxPatch.apply: DB record status is MERGED -> ALREADY_MERGED, no git apply attempted."""
        db = SandboxesRepository(sandbox_workspace)
        _create_sandbox_with_change(sandbox_workspace, "sbx_merged")
        db.update_status("sbx_merged", SandboxStatus.MERGED)
        patch = SandboxPatch(sandbox_workspace, db)

        result = patch.apply("sbx_merged")

        assert result.status == SandboxApplyStatus.ALREADY_MERGED

    def test_dirty_main_repo_without_allow_dirty_returns_main_repo_dirty(self, sandbox_workspace: Path) -> None:
        """[tier-1/integration] SandboxPatch.apply: uncommitted changes in the main workspace and allow_dirty=False -> MAIN_REPO_DIRTY, apply is refused."""
        db = SandboxesRepository(sandbox_workspace)
        _create_sandbox_with_change(sandbox_workspace, "sbx_dirty_main")
        (sandbox_workspace / "uncommitted.txt").write_text("wip", encoding="utf-8")
        patch = SandboxPatch(sandbox_workspace, db)

        result = patch.apply("sbx_dirty_main")

        assert result.status == SandboxApplyStatus.MAIN_REPO_DIRTY

    def test_sandbox_with_no_changes_returns_empty_diff(self, sandbox_workspace: Path) -> None:
        """[tier-1/integration] SandboxPatch.apply: sandbox has no commits beyond base -> EMPTY_DIFF, nothing applied."""
        db = SandboxesRepository(sandbox_workspace)
        lifecycle = SandboxLifecycle(sandbox_workspace, db)
        create_result = lifecycle.create(session_id="sbx_empty_apply")
        assert create_result.session is not None
        patch = SandboxPatch(sandbox_workspace, db)

        result = patch.apply("sbx_empty_apply")

        assert result.status == SandboxApplyStatus.EMPTY_DIFF

    def test_dry_run_reports_ok_without_modifying_main_workspace(self, sandbox_workspace: Path) -> None:
        """[tier-1/integration] SandboxPatch.apply: dry_run=True -> OK with a dry-run warning, and the target file is not created in the main workspace."""
        db = SandboxesRepository(sandbox_workspace)
        _create_sandbox_with_change(sandbox_workspace, "sbx_dry_run")
        patch = SandboxPatch(sandbox_workspace, db)

        result = patch.apply("sbx_dry_run", dry_run=True)

        assert result.status == SandboxApplyStatus.OK
        assert any("dry run" in w.lower() for w in result.warnings)
        assert not (sandbox_workspace / "new_file.txt").exists()

    def test_patch_strategy_applies_changes_and_marks_sandbox_merged(self, sandbox_workspace: Path) -> None:
        """[tier-1/integration] SandboxPatch.apply: PATCH strategy applies the sandbox's file change into the main workspace and updates DB status to MERGED."""
        db = SandboxesRepository(sandbox_workspace)
        _create_sandbox_with_change(sandbox_workspace, "sbx_apply_patch")
        patch = SandboxPatch(sandbox_workspace, db)

        result = patch.apply("sbx_apply_patch", strategy=SandboxApplyStrategy.PATCH)

        assert result.status == SandboxApplyStatus.OK
        assert (sandbox_workspace / "new_file.txt").read_text(encoding="utf-8") == "sandbox change\n"
        record = db.get("sbx_apply_patch")
        assert record is not None
        assert record.status == SandboxStatus.MERGED

    def test_squash_strategy_creates_single_commit_with_default_message(self, sandbox_workspace: Path) -> None:
        """[tier-1/integration] SandboxPatch.apply: SQUASH strategy without an explicit message commits with the default 'wt: apply changes from sandbox <id>' message and returns the new commit sha."""
        db = SandboxesRepository(sandbox_workspace)
        _create_sandbox_with_change(sandbox_workspace, "sbx_squash")
        patch = SandboxPatch(sandbox_workspace, db)

        result = patch.apply("sbx_squash", strategy=SandboxApplyStrategy.SQUASH)

        assert result.status == SandboxApplyStatus.OK
        assert result.commit_sha is not None
        log_message = GitRunner.run(["log", "-1", "--format=%s"], path=sandbox_workspace).strip()
        assert log_message == "wt: apply changes from sandbox sbx_squash"
