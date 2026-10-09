"""End-to-end tests for dovo git worktree lifecycle and operations."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from e2e.conftest import DovoPtyRunner, DovoRunner


@pytest.mark.e2e
@pytest.mark.fixture
class WorktreeCliTests:
    """E2E tests for git worktree creation, inspection, diffing, patch/squash apply, and lifecycle management."""

    def test_worktree_create_creates_real_git_worktree(self, run_dovo: DovoRunner, initialized_project: Path) -> None:
        """Scenario: Create a real Git worktree through the executable.

        Given an initialized Dovo workspace
        When dovo worktree create is executed with --format json
        Then the command exits 0, creates the worktree directory and branch, and returns a WorktreeCreateResult envelope
        """
        result = run_dovo(["worktree", "create", "--name", "first-wt", "--format", "json"], cwd=initialized_project)
        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope.get("event_type") == "WorktreeCreateResult"
        payload = envelope.get("payload", {})
        session = payload.get("session", {})
        session_id = session.get("session_id")
        assert session_id

        wt_dir = initialized_project / ".dovo" / "worktrees" / session_id
        assert wt_dir.is_dir()
        assert (wt_dir / ".git").is_file()

        branches_proc = subprocess.run(
            ["git", "branch", "--list", f"dovo/{session_id}"],
            cwd=initialized_project,
            capture_output=True,
            text=True,
            check=True,
        )
        assert f"dovo/{session_id}" in branches_proc.stdout

    def test_worktree_create_wip_overlays_uncommitted_changes(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """Scenario: Create a worktree overlaying uncommitted changes from the main workspace.

        Given an initialized workspace with uncommitted changes
        When dovo worktree create --wip is executed
        Then the uncommitted changes appear in the new worktree and remain in the main workspace
        """
        wip_file = initialized_project / "wip_test.txt"
        wip_file.write_text("uncommitted wip content\n", encoding="utf-8")

        result = run_dovo(["worktree", "create", "--wip", "--format", "json"], cwd=initialized_project)
        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        session = envelope["payload"]["session"]
        assert session["wip_applied"] is True

        wt_dir = initialized_project / ".dovo" / "worktrees" / session["session_id"]
        wt_wip_file = wt_dir / "wip_test.txt"
        assert wt_wip_file.is_file()
        assert wt_wip_file.read_text(encoding="utf-8") == "uncommitted wip content\n"

        assert wip_file.is_file()
        assert wip_file.read_text(encoding="utf-8") == "uncommitted wip content\n"

    def test_worktree_create_base_ref_targets_specified_branch(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """Scenario: Create a worktree based on a custom git branch or ref.

        Given an initialized workspace with a dedicated git branch
        When dovo worktree create --base-ref <branch> is executed
        Then the created worktree bases its commit on that branch
        """
        subprocess.run(["git", "branch", "custom-base"], cwd=initialized_project, check=True)
        rev_proc = subprocess.run(
            ["git", "rev-parse", "custom-base"],
            cwd=initialized_project,
            capture_output=True,
            text=True,
            check=True,
        )
        expected_commit = rev_proc.stdout.strip()

        result = run_dovo(
            ["worktree", "create", "--base-ref", "custom-base", "--format", "json"],
            cwd=initialized_project,
        )
        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        session = envelope["payload"]["session"]
        assert session["base_commit"] == expected_commit

    def test_worktree_list_lists_tracked_worktrees_and_filters_status(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """Scenario: List tracked worktrees and filter by lifecycle status.

        Given an initialized workspace with an active worktree
        When dovo worktree list is run bare or with --status filters
        Then active worktrees are listed and non-matching filters exclude them
        """
        create_res = run_dovo(["worktree", "create", "--name", "list-wt", "--format", "json"], cwd=initialized_project)
        session_id = json.loads(create_res.stdout)["payload"]["session"]["session_id"]

        list_all = run_dovo(["worktree", "list", "--format", "json"], cwd=initialized_project)
        assert list_all.exit_code == 0
        all_ids = [w["id"] for w in json.loads(list_all.stdout)["payload"]["worktrees"]]
        assert session_id in all_ids

        list_active = run_dovo(["worktree", "list", "--status", "active", "--format", "json"], cwd=initialized_project)
        assert list_active.exit_code == 0
        active_ids = [w["id"] for w in json.loads(list_active.stdout)["payload"]["worktrees"]]
        assert session_id in active_ids

        list_cleaned = run_dovo(
            ["worktree", "list", "--status", "cleaned", "--format", "json"], cwd=initialized_project
        )
        assert list_cleaned.exit_code == 0
        cleaned_ids = [w["id"] for w in json.loads(list_cleaned.stdout)["payload"]["worktrees"]]
        assert session_id not in cleaned_ids

    def test_worktree_show_returns_metadata_and_branch_tracking(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """Scenario: Show metadata and branch tracking for a tracked worktree.

        Given an initialized workspace with a tracked worktree
        When dovo worktree show <worktree_id> --format json is executed
        Then the command exits 0 and returns an envelope detailing the worktree
        """
        create_res = run_dovo(["worktree", "create", "--name", "show-wt", "--format", "json"], cwd=initialized_project)
        session_id = json.loads(create_res.stdout)["payload"]["session"]["session_id"]

        result = run_dovo(["worktree", "show", session_id, "--format", "json"], cwd=initialized_project)
        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope.get("event_type") == "WorktreeShowResult"
        payload = envelope.get("payload", {})
        assert payload.get("disk_present") is True
        worktree = payload.get("worktree", {})
        assert worktree.get("id") == session_id
        assert worktree.get("branch_name") == f"dovo/{session_id}"

    def test_worktree_diff_reports_changed_file_in_worktree(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """Scenario: Inspect unified diff of modified files in a worktree.

        Given an initialized workspace and a worktree containing a modified file
        When dovo worktree diff <worktree_id> --format json is executed
        Then the command exits 0 and reports the modified file and diff hunk
        """
        create_res = run_dovo(["worktree", "create", "--name", "diff-wt", "--format", "json"], cwd=initialized_project)
        session_id = json.loads(create_res.stdout)["payload"]["session"]["session_id"]
        wt_dir = initialized_project / ".dovo" / "worktrees" / session_id

        (wt_dir / "diff_file.py").write_text("print('diff test')\n", encoding="utf-8")

        result = run_dovo(["worktree", "diff", session_id, "--format", "json"], cwd=initialized_project)
        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope.get("event_type") == "WorktreeDiffResult"
        payload = envelope.get("payload", {})
        assert "diff_file.py" in payload.get("files_changed", [])
        assert "diff_file.py" in payload.get("diff_text", "")

    def test_worktree_diff_stat_outputs_diffstat_summary(self, run_dovo: DovoRunner, initialized_project: Path) -> None:
        """Scenario: Inspect diffstat summary metrics of a worktree.

        Given an initialized workspace and a worktree containing a modified file
        When dovo worktree diff <worktree_id> --stat --format json is executed
        Then the command exits 0 and stat_text contains diffstat summary metrics
        """
        create_res = run_dovo(["worktree", "create", "--name", "stat-wt", "--format", "json"], cwd=initialized_project)
        session_id = json.loads(create_res.stdout)["payload"]["session"]["session_id"]
        wt_dir = initialized_project / ".dovo" / "worktrees" / session_id

        (wt_dir / "stat_file.py").write_text("print('stat test')\n", encoding="utf-8")

        result = run_dovo(["worktree", "diff", session_id, "--stat", "--format", "json"], cwd=initialized_project)
        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        payload = envelope.get("payload", {})
        assert "stat_file.py" in payload.get("stat_text", "")

    def test_worktree_apply_transfers_changes_as_uncommitted_patch(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """Scenario: Apply worktree changes back to the main workspace as an uncommitted patch.

        Given an initialized workspace and a worktree with modified files
        When dovo worktree apply <worktree_id> is executed with default patch strategy
        Then changes are transferred to the main workspace as uncommitted modifications
        """
        create_res = run_dovo(["worktree", "create", "--name", "patch-wt", "--format", "json"], cwd=initialized_project)
        session_id = json.loads(create_res.stdout)["payload"]["session"]["session_id"]
        wt_dir = initialized_project / ".dovo" / "worktrees" / session_id

        (wt_dir / "patch_result.txt").write_text("applied patch content\n", encoding="utf-8")

        result = run_dovo(["worktree", "apply", session_id, "--format", "json"], cwd=initialized_project)
        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope.get("event_type") == "WorktreeApplyResult"
        payload = envelope.get("payload", {})
        assert payload.get("strategy") == "patch"
        assert "patch_result.txt" in payload.get("touched_files", [])

        main_file = initialized_project / "patch_result.txt"
        assert main_file.is_file()
        assert main_file.read_text(encoding="utf-8") == "applied patch content\n"

        status_proc = subprocess.run(
            ["git", "status", "--porcelain"], cwd=initialized_project, capture_output=True, text=True, check=True
        )
        assert "patch_result.txt" in status_proc.stdout

    def test_worktree_apply_strategy_squash_creates_commit(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """Scenario: Apply worktree changes back to the main workspace as a squashed commit.

        Given an initialized workspace and a worktree with modified files
        When dovo worktree apply <worktree_id> --strategy squash -m "<msg>" is executed
        Then changes are committed to the main branch in a single commit with the given message
        """
        create_res = run_dovo(
            ["worktree", "create", "--name", "squash-wt", "--format", "json"], cwd=initialized_project
        )
        session_id = json.loads(create_res.stdout)["payload"]["session"]["session_id"]
        wt_dir = initialized_project / ".dovo" / "worktrees" / session_id

        (wt_dir / "squash_result.txt").write_text("squashed content\n", encoding="utf-8")

        result = run_dovo(
            [
                "worktree",
                "apply",
                session_id,
                "--strategy",
                "squash",
                "-m",
                "feat: squashed feature commit",
                "--format",
                "json",
            ],
            cwd=initialized_project,
        )
        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        payload = envelope.get("payload", {})
        assert payload.get("strategy") == "squash"
        assert payload.get("commit_sha") is not None

        log_proc = subprocess.run(
            ["git", "log", "-1", "--pretty=%s"], cwd=initialized_project, capture_output=True, text=True, check=True
        )
        assert log_proc.stdout.strip() == "feat: squashed feature commit"
        assert (initialized_project / "squash_result.txt").read_text(encoding="utf-8") == "squashed content\n"

    def test_worktree_apply_dry_run_checks_conflicts_without_mutating_workspace(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """Scenario: Dry-run worktree application without mutating the main workspace.

        Given an initialized workspace and a worktree with modified files
        When dovo worktree apply <worktree_id> --dry-run is executed
        Then potential changes are validated and the main workspace remains unmutated
        """
        create_res = run_dovo(["worktree", "create", "--name", "dry-wt", "--format", "json"], cwd=initialized_project)
        session_id = json.loads(create_res.stdout)["payload"]["session"]["session_id"]
        wt_dir = initialized_project / ".dovo" / "worktrees" / session_id

        (wt_dir / "dry_run_file.txt").write_text("dry run content\n", encoding="utf-8")

        result = run_dovo(["worktree", "apply", session_id, "--dry-run", "--format", "json"], cwd=initialized_project)
        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        payload = envelope.get("payload", {})
        assert "dry_run_file.txt" in payload.get("touched_files", [])

        assert not (initialized_project / "dry_run_file.txt").exists()

    def test_worktree_apply_dirty_handling_with_and_without_allow_dirty(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """Scenario: Worktree apply rejects dirty main repo without --allow-dirty, and succeeds with --allow-dirty.

        Given an initialized workspace with uncommitted changes and a worktree with changes
        When dovo worktree apply is executed without --allow-dirty it exits 1, and with --allow-dirty it exits 0
        Then uncommitted changes in the main workspace safely guard against accidental patch overlays
        """
        create_res = run_dovo(["worktree", "create", "--name", "dirty-wt", "--format", "json"], cwd=initialized_project)
        session_id = json.loads(create_res.stdout)["payload"]["session"]["session_id"]
        wt_dir = initialized_project / ".dovo" / "worktrees" / session_id

        (wt_dir / "wt_patch.txt").write_text("wt content\n", encoding="utf-8")

        # Dirty the main workspace
        (initialized_project / "dirty_local.txt").write_text("local dirty content\n", encoding="utf-8")

        # Fails without --allow-dirty
        res_reject = run_dovo(["worktree", "apply", session_id, "--format", "json"], cwd=initialized_project)
        assert res_reject.exit_code == 1
        assert json.loads(res_reject.stdout)["payload"]["status"] == "main_repo_dirty"

        # Succeeds with --allow-dirty
        res_allow = run_dovo(
            ["worktree", "apply", session_id, "--allow-dirty", "--format", "json"],
            cwd=initialized_project,
        )
        assert res_allow.exit_code == 0
        assert (initialized_project / "wt_patch.txt").is_file()

    def test_worktree_apply_delete_cleans_up_worktree_and_branch(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """Scenario: Apply worktree changes and clean up worktree and branch.

        Given an initialized workspace and a worktree with modified files
        When dovo worktree apply <worktree_id> --delete is executed
        Then changes are applied, the worktree directory is removed, and its branch is deleted
        """
        create_res = run_dovo(["worktree", "create", "--name", "del-wt", "--format", "json"], cwd=initialized_project)
        session_id = json.loads(create_res.stdout)["payload"]["session"]["session_id"]
        wt_dir = initialized_project / ".dovo" / "worktrees" / session_id

        (wt_dir / "del_applied.txt").write_text("applied and deleted\n", encoding="utf-8")

        result = run_dovo(["worktree", "apply", session_id, "--delete", "--format", "json"], cwd=initialized_project)
        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope["payload"]["cleaned_up"] is True
        assert not wt_dir.exists()

        branch_proc = subprocess.run(
            ["git", "branch", "--list", f"dovo/{session_id}"],
            cwd=initialized_project,
            capture_output=True,
            text=True,
            check=True,
        )
        assert branch_proc.stdout.strip() == ""

    def test_worktree_delete_force_removes_worktree_and_branch(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """Scenario: Delete a worktree and its branch with --force flag.

        Given an initialized workspace with a tracked worktree
        When dovo worktree delete <worktree_id> --force is executed
        Then the worktree directory is deleted from disk and its branch is removed
        """
        create_res = run_dovo(
            ["worktree", "create", "--name", "force-del-wt", "--format", "json"], cwd=initialized_project
        )
        session_id = json.loads(create_res.stdout)["payload"]["session"]["session_id"]
        wt_dir = initialized_project / ".dovo" / "worktrees" / session_id

        result = run_dovo(["worktree", "delete", session_id, "--force", "--format", "json"], cwd=initialized_project)
        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope["payload"]["deleted"] is True
        assert not wt_dir.exists()

        branch_proc = subprocess.run(
            ["git", "branch", "--list", f"dovo/{session_id}"],
            cwd=initialized_project,
            capture_output=True,
            text=True,
            check=True,
        )
        assert branch_proc.stdout.strip() == ""

    def test_worktree_delete_interactive_prompt_confirms_and_aborts(
        self, run_dovo_pty: DovoPtyRunner, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """Scenario: Interactive prompt for worktree deletion confirms with y and aborts with n.

        Given an initialized workspace with a tracked worktree
        When dovo worktree delete is executed in an interactive PTY
        Then answering n aborts deletion and answering y confirms deletion
        """
        create_res = run_dovo(
            ["worktree", "create", "--name", "pty-del-wt", "--format", "json"], cwd=initialized_project
        )
        session_id = json.loads(create_res.stdout)["payload"]["session"]["session_id"]
        wt_dir = initialized_project / ".dovo" / "worktrees" / session_id

        # Abort with 'n'
        abort_res = run_dovo_pty(
            ["worktree", "delete", session_id],
            cwd=initialized_project,
            prompt_replies=[("[y/N]", "n\n")],
        )
        assert abort_res.exit_code == 1
        assert "Aborted." in abort_res.transcript
        assert wt_dir.is_dir()

        # Confirm with 'y'
        confirm_res = run_dovo_pty(
            ["worktree", "delete", session_id],
            cwd=initialized_project,
            prompt_replies=[("[y/N]", "y\n")],
        )
        assert confirm_res.exit_code == 0
        assert "Worktree deleted:" in confirm_res.transcript
        assert not wt_dir.exists()

    def test_worktree_prune_dry_run_detects_stale_worktree_without_mutation(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """Scenario: Detect stale worktrees without mutation using dry-run.

        Given an initialized workspace with a stale worktree whose directory was removed
        When dovo worktree prune --dry-run --format json is executed
        Then stale resources are reported and no disk or branch mutations occur
        """
        create_res = run_dovo(
            ["worktree", "create", "--name", "stale-dry-wt", "--format", "json"], cwd=initialized_project
        )
        session_id = json.loads(create_res.stdout)["payload"]["session"]["session_id"]
        wt_dir = initialized_project / ".dovo" / "worktrees" / session_id

        shutil.rmtree(wt_dir)

        result = run_dovo(["worktree", "prune", "--dry-run", "--format", "json"], cwd=initialized_project)
        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        payload = envelope.get("payload", {})
        assert payload.get("dry_run") is True
        assert len(payload.get("items", [])) > 0

        branch_proc = subprocess.run(
            ["git", "branch", "--list", f"dovo/{session_id}"],
            cwd=initialized_project,
            capture_output=True,
            text=True,
            check=True,
        )
        assert f"dovo/{session_id}" in branch_proc.stdout

    def test_worktree_prune_removes_stale_worktree_and_branch(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """Scenario: Prune stale worktrees and temporary branches.

        Given an initialized workspace with an orphaned worktree directory
        When dovo worktree prune --format json is executed
        Then stale worktree registrations and temporary branches are removed
        """
        create_res = run_dovo(
            ["worktree", "create", "--name", "stale-prune-wt", "--format", "json"], cwd=initialized_project
        )
        session_id = json.loads(create_res.stdout)["payload"]["session"]["session_id"]
        wt_dir = initialized_project / ".dovo" / "worktrees" / session_id

        shutil.rmtree(wt_dir)

        result = run_dovo(["worktree", "prune", "--format", "json"], cwd=initialized_project)
        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        payload = envelope.get("payload", {})
        assert payload.get("dry_run") is False
        assert payload.get("pruned_count", 0) > 0

        branch_proc = subprocess.run(
            ["git", "branch", "--list", f"dovo/{session_id}"],
            cwd=initialized_project,
            capture_output=True,
            text=True,
            check=True,
        )
        assert branch_proc.stdout.strip() == ""
