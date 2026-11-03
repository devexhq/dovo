"""Tier 1 contract: .dovo/catalog/scripts/sdlc/ship/ship_state.py dispatches stages, records transitions, gates publication, and writes reports."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from tests.lint.astlib import REPO_ROOT

HEAD_SHA = "a" * 40
NEW_HEAD_SHA = "b" * 40
PR_HEAD_SHA = "c" * 40
ISSUE_BODY = "## Why\nGrounded ticket body.\n"
ISSUE_BODY_SHA = hashlib.sha256(ISSUE_BODY.encode("utf-8")).hexdigest()
GH_ISSUE_VIEW = ("gh", "api", "repos/{owner}/{repo}/issues/1367")
GH_PR_LIST = ("gh", "api", "--paginate")


def _load_ship_state() -> ModuleType:
    """Load .dovo/catalog/scripts/sdlc/ship/ship_state.py by file path."""
    script_path = REPO_ROOT / ".dovo/catalog/scripts/sdlc/ship/ship_state.py"
    spec = importlib.util.spec_from_file_location("sdlc_ship_state", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["sdlc_ship_state"] = module
    spec.loader.exec_module(module)
    return module


SHIP = _load_ship_state()


def _completed(
    args: Sequence[str], *, stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    """Build a CompletedProcess for fake subprocess.run responses."""
    return subprocess.CompletedProcess(args=list(args), returncode=returncode, stdout=stdout, stderr=stderr)


def _pr_command_stdout(
    cmd: tuple[str, ...],
    *,
    cwd: Path | None,
    other_wt: Path,
    dirty_stdout: str,
    wt_head: str,
    responses: dict[tuple[str, ...], str],
) -> str:
    """Return fake stdout for one git/gh command during PR continuation tests."""
    if cmd[:3] in responses:
        return responses[cmd[:3]]
    if cmd[:2] == ("git", "status") and cwd == other_wt:
        return dirty_stdout
    if cmd[:3] == ("git", "rev-parse", "HEAD"):
        return f"{wt_head if cwd == other_wt else PR_HEAD_SHA}\n"
    return ""


def _mock_pr_continuation(
    monkeypatch: pytest.MonkeyPatch,
    *,
    workspace: Path,
    other_wt: Path,
    dirty_stdout: str = "",
    wt_head: str = PR_HEAD_SHA,
    recorded_commands: list[tuple[str, ...]] | None = None,
) -> None:
    """Patch subprocess.run for PR continuation tests with an existing kept worktree."""
    porcelain = (
        f"worktree {workspace.resolve()}\nHEAD {HEAD_SHA}\nbranch refs/heads/dovo/dovo_new\n\n"
        f"worktree {other_wt}\nHEAD {wt_head}\nbranch refs/heads/dovo/dovo_old\n\n"
    )
    responses: dict[tuple[str, ...], str] = {
        GH_ISSUE_VIEW: json.dumps(
            {
                "number": 1367,
                "title": "Deterministic ship state",
                "body": ISSUE_BODY,
                "html_url": "https://github.com/devexhq/dovo/issues/1367",
            }
        ),
        ("gh", "api", "repos/{owner}/{repo}/pulls/42"): json.dumps(
            {
                "number": 42,
                "head": {"ref": "dovo/dovo_old", "sha": PR_HEAD_SHA},
                "base": {"ref": "main"},
                "html_url": "https://github.com/devexhq/dovo/pull/42",
                "state": "open",
            }
        ),
        ("git", "worktree", "list"): porcelain,
    }

    def fake_run(
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        check: bool = True,
        capture_output: bool = True,
        text: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del check, capture_output, text
        cmd = tuple(args)
        if recorded_commands is not None:
            recorded_commands.append(cmd)
        out = _pr_command_stdout(
            cmd, cwd=cwd, other_wt=other_wt, dirty_stdout=dirty_stdout, wt_head=wt_head, responses=responses
        )
        return _completed(cmd, stdout=out)

    monkeypatch.setattr(subprocess, "run", fake_run)


def _mock_git_and_issue(
    monkeypatch: pytest.MonkeyPatch,
    *,
    head_sha: str = HEAD_SHA,
    issue_body: str = ISSUE_BODY,
) -> None:
    """Patch subprocess.run at the boundary for git rev-parse HEAD and the gh api issue lookup."""

    def fake_subprocess_run(
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        check: bool = True,
        capture_output: bool = True,
        text: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, check, capture_output, text
        cmd = tuple(args)
        if cmd[:3] == ("git", "rev-parse", "HEAD"):
            return _completed(cmd, stdout=f"{head_sha}\n")
        if cmd[:3] == GH_ISSUE_VIEW:
            return _completed(
                cmd,
                stdout=json.dumps(
                    {
                        "number": 1367,
                        "title": "Deterministic ship state",
                        "body": issue_body,
                        "html_url": "https://github.com/devexhq/dovo/issues/1367",
                    }
                ),
            )
        raise AssertionError(f"Unexpected subprocess command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a working directory with cleared ship environment variables."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DOVO_OUTPUT", raising=False)
    monkeypatch.delenv("SHIP_ISSUE", raising=False)
    monkeypatch.delenv("SHIP_PULL_REQUEST", raising=False)
    monkeypatch.delenv("SHIP_ACTION", raising=False)
    monkeypatch.delenv("SHIP_QUESTION_ID", raising=False)
    monkeypatch.delenv("SHIP_ANSWER", raising=False)
    return tmp_path


def _seed_prepared_state(root: Path, *, head_sha: str = HEAD_SHA, body_sha: str = ISSUE_BODY_SHA) -> None:
    """Write baseline contract.json and progress.json in root."""
    ship_dir = root / ".agentic" / "ship"
    ship_dir.mkdir(parents=True, exist_ok=True)
    contract = {
        "schema_version": 1,
        "issue": {
            "number": 1367,
            "title": "Deterministic ship state",
            "body": ISSUE_BODY,
            "body_sha": body_sha,
            "url": "https://github.com/devexhq/dovo/issues/1367",
        },
        "pull_request": None,
        "base_sha": head_sha,
        "head_sha": head_sha,
    }
    progress = SHIP.initial_progress(1367, body_sha, head_sha)
    (ship_dir / "contract.json").write_text(json.dumps(contract), encoding="utf-8")
    (ship_dir / "progress.json").write_text(json.dumps(progress), encoding="utf-8")


def _write_stage(
    root: Path,
    stage: str,
    disposition: str,
    *,
    summary: str = "Stage summary",
    body_sha: str = ISSUE_BODY_SHA,
    head_sha: str = HEAD_SHA,
    report_path: str = "",
    questions: list[dict[str, object]] | None = None,
) -> None:
    """Write .agentic/ship/stage.json in root."""
    payload: dict[str, object] = {
        "stage": stage,
        "disposition": disposition,
        "summary": summary,
        "body_sha": body_sha,
        "head_sha": head_sha,
    }
    if report_path:
        payload["report_path"] = report_path
    if questions is not None:
        payload["questions"] = questions
    stage_file = root / ".agentic" / "ship" / "stage.json"
    stage_file.parent.mkdir(parents=True, exist_ok=True)
    stage_file.write_text(json.dumps(payload), encoding="utf-8")


def _read_json(path: Path) -> dict[str, object]:
    """Read and parse a JSON object from path."""
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


class PrepareTests:
    def test_prepare_new_issue_writes_contract_and_initial_progress(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        output_file = workspace / "dovo_output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output_file))

        def fake_run(
            args: Sequence[str],
            *,
            cwd: Path | None = None,
            check: bool = True,
            capture_output: bool = True,
            text: bool = True,
        ) -> subprocess.CompletedProcess[str]:
            del cwd, check, capture_output, text
            cmd = tuple(args)
            if cmd[:3] == GH_ISSUE_VIEW:
                return _completed(
                    cmd,
                    stdout=json.dumps(
                        {
                            "number": 1367,
                            "title": "Deterministic ship state",
                            "body": ISSUE_BODY,
                            "html_url": "https://github.com/devexhq/dovo/issues/1367",
                        }
                    ),
                )
            if cmd[:3] == GH_PR_LIST:
                return _completed(cmd, stdout="")
            if cmd[:2] == ("git", "status"):
                return _completed(cmd, stdout="")
            if cmd[:3] == ("git", "fetch", "-q"):
                return _completed(cmd)
            if cmd[:3] == ("git", "rev-parse", "HEAD"):
                return _completed(cmd, stdout=f"{HEAD_SHA}\n")
            raise AssertionError(f"Unexpected command: {cmd}")

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert SHIP.main(["prepare", "--issue", "1367"]) == 0

        stdout_data = json.loads(capsys.readouterr().out)
        assert stdout_data == {
            "issue": "1367",
            "body_sha": ISSUE_BODY_SHA,
            "head_sha": HEAD_SHA,
            "pr_number": "",
        }
        assert (
            output_file.read_text(encoding="utf-8")
            == f"issue=1367\nbody_sha={ISSUE_BODY_SHA}\nhead_sha={HEAD_SHA}\npr_number=\n"
        )
        contract = _read_json(workspace / ".agentic/ship/contract.json")
        assert contract["issue"] == {
            "number": 1367,
            "title": "Deterministic ship state",
            "body": ISSUE_BODY,
            "body_sha": ISSUE_BODY_SHA,
            "url": "https://github.com/devexhq/dovo/issues/1367",
        }
        assert contract["pull_request"] is None
        progress = _read_json(workspace / ".agentic/ship/progress.json")
        assert progress["next_action"] == "assess"
        assert progress["outcome"] == "working"

    def test_prepare_preserves_compatible_progress_on_resume(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_prepared_state(workspace)
        progress_path = workspace / ".agentic/ship/progress.json"
        existing = _read_json(progress_path)
        existing["next_action"] = "review"
        existing["review_rounds"] = 2
        progress_path.write_text(json.dumps(existing), encoding="utf-8")

        def fake_run(
            args: Sequence[str],
            *,
            cwd: Path | None = None,
            check: bool = True,
            capture_output: bool = True,
            text: bool = True,
        ) -> subprocess.CompletedProcess[str]:
            del cwd, check, capture_output, text
            cmd = tuple(args)
            if cmd[:3] == GH_ISSUE_VIEW:
                return _completed(
                    cmd,
                    stdout=json.dumps(
                        {
                            "number": 1367,
                            "title": "Deterministic ship state",
                            "body": ISSUE_BODY,
                            "html_url": "https://github.com/devexhq/dovo/issues/1367",
                        }
                    ),
                )
            if cmd[:3] == GH_PR_LIST:
                return _completed(cmd, stdout="")
            if cmd[:3] == ("git", "rev-parse", "HEAD"):
                return _completed(cmd, stdout=f"{NEW_HEAD_SHA}\n")
            raise AssertionError(f"Unexpected command: {cmd}")

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert SHIP.main(["prepare", "--issue", "1367"]) == 0
        updated = _read_json(progress_path)
        assert updated["next_action"] == "review"
        assert updated["review_rounds"] == 2
        assert updated["head_sha"] == NEW_HEAD_SHA

    def test_prepare_existing_pr_fetches_and_resets_to_fetch_head_when_kept_worktree_matches(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        other_wt = (workspace.parent / "kept_wt").resolve()
        commands_run: list[tuple[str, ...]] = []
        _mock_pr_continuation(monkeypatch, workspace=workspace, other_wt=other_wt, recorded_commands=commands_run)

        assert SHIP.main(["prepare", "--issue", "1367", "--pull-request", "42"]) == 0
        assert ("git", "fetch", "-q", "origin", "refs/heads/dovo/dovo_old") in commands_run
        assert ("git", "reset", "-q", "--hard", "FETCH_HEAD") in commands_run
        contract = _read_json(workspace / ".agentic/ship/contract.json")
        assert contract["pull_request"] == {
            "pr_number": 42,
            "branch": "dovo/dovo_old",
            "head_sha": PR_HEAD_SHA,
            "base_branch": "main",
            "url": "https://github.com/devexhq/dovo/pull/42",
        }

    @pytest.mark.parametrize(
        ("dirty_stdout", "wt_head", "expected_fragment"),
        [
            pytest.param(" M src/dovo/app.py\n", PR_HEAD_SHA, "uncommitted changes", id="dirty-kept-worktree"),
            pytest.param("", "d" * 40, "differs from remote PR head", id="ahead-kept-worktree"),
        ],
    )
    def test_prepare_refuses_when_kept_worktree_on_pr_branch_is_dirty_or_ahead(
        self,
        dirty_stdout: str,
        wt_head: str,
        expected_fragment: str,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        other_wt = (workspace.parent / "kept_wt").resolve()
        _mock_pr_continuation(
            monkeypatch, workspace=workspace, other_wt=other_wt, dirty_stdout=dirty_stdout, wt_head=wt_head
        )

        assert SHIP.main(["prepare", "--issue", "1367", "--pull-request", "42"]) == 1
        err = capsys.readouterr().err
        assert str(other_wt) in err
        assert "dovo/dovo_old" in err
        assert expected_fragment in err


class NextAndRecordTransitionTests:
    def test_next_removes_stale_stage_json_and_outputs_action(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _seed_prepared_state(workspace)
        _write_stage(workspace, "assess", "implement")
        output_file = workspace / "dovo_output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output_file))

        assert SHIP.main(["next"]) == 0
        assert not (workspace / ".agentic/ship/stage.json").exists()
        assert json.loads(capsys.readouterr().out) == {"action": "assess", "question_id": "", "question": ""}
        assert output_file.read_text(encoding="utf-8") == "action=assess\nquestion_id=\nquestion=\n"

    @pytest.mark.parametrize(
        ("stage", "disposition", "expected_outcome", "expected_next"),
        [
            ("assess", "investigate", "working", "investigate"),
            ("assess", "plan", "working", "plan"),
            ("assess", "implement", "working", "implement"),
            ("assess", "no_action", "no_action", ""),
            ("assess", "blocked", "blocked", ""),
            ("investigate", "plan", "working", "plan"),
            ("investigate", "implement", "working", "implement"),
            ("plan", "implement", "working", "implement"),
            ("implement", "validate", "working", "validate"),
            ("implement", "completed", "working", "validate"),
            ("validate", "passed", "working", "review"),
            ("validate", "checks_failed", "working", "implement"),
            ("review", "changes_requested", "working", "implement"),
            ("review", "plan", "working", "plan"),
        ],
    )
    def test_record_transitions_across_stages_and_ignores_stale_ship_answer(
        self,
        stage: str,
        disposition: str,
        expected_outcome: str,
        expected_next: str,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _seed_prepared_state(workspace)
        _mock_git_and_issue(monkeypatch, head_sha=HEAD_SHA)
        report_file = workspace / ".agentic/ship/temp-report.md"
        report_file.write_text("# Stage report\n", encoding="utf-8")
        _write_stage(workspace, stage, disposition, report_path=str(report_file))

        assert (
            SHIP.main(
                [
                    "record",
                    "--action",
                    stage,
                    "--question-id",
                    "stale-q-1",
                    "--answer",
                    "stale answer from previous iteration",
                ]
            )
            == 0
        )
        assert json.loads(capsys.readouterr().out) == {"outcome": expected_outcome, "next_action": expected_next}
        assert not (workspace / ".agentic/ship/stage.json").exists()
        assert (workspace / f".agentic/ship/reports/01-{stage}.md").is_file()
        progress = _read_json(workspace / ".agentic/ship/progress.json")
        assert progress["answered_questions"] == []

    def test_review_ready_after_validate_passed_at_same_head_produces_ready(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_prepared_state(workspace)
        _mock_git_and_issue(monkeypatch, head_sha=HEAD_SHA)

        _write_stage(workspace, "validate", "passed", summary="All checks passed")
        assert SHIP.main(["record", "--action", "validate"]) == 0

        _write_stage(workspace, "review", "ready", summary="LGTM")
        assert SHIP.main(["record", "--action", "review"]) == 0

        progress = _read_json(workspace / ".agentic/ship/progress.json")
        assert progress["outcome"] == "ready"
        assert progress["next_action"] == ""

    def test_serial_questions_presented_one_per_iteration_and_returned_to_origin_stage(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _seed_prepared_state(workspace)
        _mock_git_and_issue(monkeypatch, head_sha=HEAD_SHA)
        _write_stage(
            workspace,
            "plan",
            "ask",
            questions=[
                {
                    "id": "Q-1",
                    "question": "Should we drop the legacy alias?",
                    "evidence": "Only one internal caller remains.",
                    "recommendation": "Drop the alias now.",
                },
                {
                    "id": "Q-2",
                    "question": "Should we cap retries at 3?",
                    "report_markdown": "# Custom Q-2 Markdown\n\nCap at 3.",
                },
            ],
        )

        assert SHIP.main(["record", "--action", "plan"]) == 0
        capsys.readouterr()

        # First ask iteration presents Q-1 only
        assert SHIP.main(["next"]) == 0
        next_1 = json.loads(capsys.readouterr().out)
        assert next_1 == {
            "action": "ask",
            "question_id": "Q-1",
            "question": "Should we drop the legacy alias?",
        }
        assert "Recommendation:** Drop the alias now." in (workspace / ".agentic/ship/question.md").read_text(
            encoding="utf-8"
        )

        assert SHIP.main(["record", "--action", "ask", "--question-id", "Q-1", "--answer", "Yes, drop the alias."]) == 0
        rec_1 = json.loads(capsys.readouterr().out)
        assert rec_1 == {"outcome": "working", "next_action": "ask"}

        # Second ask iteration presents Q-2
        assert SHIP.main(["next"]) == 0
        next_2 = json.loads(capsys.readouterr().out)
        assert next_2 == {
            "action": "ask",
            "question_id": "Q-2",
            "question": "Should we cap retries at 3?",
        }
        assert "# Custom Q-2 Markdown" in (workspace / ".agentic/ship/question.md").read_text(encoding="utf-8")

        assert SHIP.main(["record", "--action", "ask", "--question-id", "Q-2", "--answer", "Yes, 3."]) == 0
        rec_2 = json.loads(capsys.readouterr().out)
        assert rec_2 == {"outcome": "working", "next_action": "plan"}

        progress = _read_json(workspace / ".agentic/ship/progress.json")
        answered = progress["answered_questions"]
        assert isinstance(answered, list)
        assert len(answered) == 2
        assert progress["pending_questions"] == []

    def test_ask_stop_answer_marks_run_blocked(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _seed_prepared_state(workspace)
        _mock_git_and_issue(monkeypatch, head_sha=HEAD_SHA)
        _write_stage(
            workspace,
            "assess",
            "ask",
            questions=[{"id": "Q-1", "question": "Proceed with breaking change?"}],
        )
        assert SHIP.main(["record", "--action", "assess"]) == 0
        capsys.readouterr()

        assert SHIP.main(["record", "--action", "ask", "--question-id", "Q-1", "--answer", "stop: defer ticket"]) == 0
        assert json.loads(capsys.readouterr().out) == {"outcome": "blocked", "next_action": ""}

    def test_record_enforces_three_round_review_cap_and_non_progress_detection(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _seed_prepared_state(workspace)

        for round_num, current_sha in enumerate(("1" * 40, "2" * 40, "3" * 40, "4" * 40), start=1):
            _mock_git_and_issue(monkeypatch, head_sha=current_sha)
            _write_stage(
                workspace,
                "review",
                "changes_requested",
                summary=f"Blocker in round {round_num}",
                head_sha=current_sha,
            )
            assert SHIP.main(["record", "--action", "review"]) == 0
            out = json.loads(capsys.readouterr().out)
            if round_num <= 3:
                assert out == {"outcome": "working", "next_action": "implement"}
            else:
                assert out == {"outcome": "incomplete", "next_action": ""}

    def test_record_stops_on_repeated_identical_validation_failure_at_same_head(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _seed_prepared_state(workspace)
        _mock_git_and_issue(monkeypatch, head_sha=HEAD_SHA)

        _write_stage(workspace, "validate", "checks_failed", summary="ruff check failed on foo.py:12")
        assert SHIP.main(["record", "--action", "validate"]) == 0
        assert json.loads(capsys.readouterr().out) == {"outcome": "working", "next_action": "implement"}

        _write_stage(workspace, "validate", "checks_failed", summary="ruff check failed on foo.py:12")
        assert SHIP.main(["record", "--action", "validate"]) == 0
        assert json.loads(capsys.readouterr().out) == {"outcome": "incomplete", "next_action": ""}

    @pytest.mark.parametrize(
        ("stage_override", "body_sha", "head_sha"),
        [
            pytest.param("review", ISSUE_BODY_SHA, HEAD_SHA, id="wrong-stage"),
            pytest.param("implement", "deadbeef", HEAD_SHA, id="stale-body-sha"),
            pytest.param("implement", ISSUE_BODY_SHA, NEW_HEAD_SHA, id="stale-head-sha"),
        ],
    )
    def test_record_rejects_mismatched_stage_envelope(
        self,
        stage_override: str,
        body_sha: str,
        head_sha: str,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _seed_prepared_state(workspace)
        _mock_git_and_issue(monkeypatch, head_sha=HEAD_SHA)
        _write_stage(workspace, stage_override, "validate", body_sha=body_sha, head_sha=head_sha)

        assert SHIP.main(["record", "--action", "implement"]) == 1


class PublicationGateAndReportTests:
    def test_publication_gate_permits_ready_when_contract_head_validate_and_review_match(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _seed_prepared_state(workspace)
        _mock_git_and_issue(monkeypatch, head_sha=HEAD_SHA, issue_body=ISSUE_BODY)

        _write_stage(workspace, "validate", "passed", summary="Checks green")
        assert SHIP.main(["record", "--action", "validate"]) == 0
        _write_stage(workspace, "review", "ready", summary="Approved")
        assert SHIP.main(["record", "--action", "review"]) == 0
        capsys.readouterr()

        assert SHIP.main(["publication-gate"]) == 0
        assert json.loads(capsys.readouterr().out) == {"outcome": "ready"}
        pub = _read_json(workspace / ".agentic/ship/publication.json")
        assert pub["permitted"] is True
        assert pub["outcome"] == "ready"

        # Report requires pr.json when publication was permitted
        assert SHIP.main(["report"]) == 1
        (workspace / ".agentic/ship/pr.json").write_text(
            json.dumps({"url": "https://github.com/devexhq/dovo/pull/1368", "head_sha": HEAD_SHA}),
            encoding="utf-8",
        )
        assert SHIP.main(["report"]) == 0
        result = _read_json(workspace / ".agentic/ship/result.json")
        assert result["status"] == "published"
        assert result["pr_url"] == "https://github.com/devexhq/dovo/pull/1368"
        assert "https://github.com/devexhq/dovo/pull/1368" in (workspace / ".agentic/ship/report.md").read_text(
            encoding="utf-8"
        )

    def test_publication_gate_normalizes_loop_exhaustion_working_to_incomplete(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _seed_prepared_state(workspace)
        _mock_git_and_issue(monkeypatch, head_sha=HEAD_SHA, issue_body=ISSUE_BODY)

        assert SHIP.main(["publication-gate"]) == 0
        assert json.loads(capsys.readouterr().out) == {"outcome": "incomplete"}
        pub = _read_json(workspace / ".agentic/ship/publication.json")
        assert pub["permitted"] is False
        assert pub["outcome"] == "incomplete"
        assert _read_json(workspace / ".agentic/ship/progress.json")["outcome"] == "incomplete"

    def test_publication_gate_blocks_when_live_issue_body_changed(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _seed_prepared_state(workspace)
        _mock_git_and_issue(monkeypatch, head_sha=HEAD_SHA, issue_body=ISSUE_BODY)
        _write_stage(workspace, "validate", "passed")
        assert SHIP.main(["record", "--action", "validate"]) == 0
        _write_stage(workspace, "review", "ready")
        assert SHIP.main(["record", "--action", "review"]) == 0
        capsys.readouterr()

        _mock_git_and_issue(monkeypatch, head_sha=HEAD_SHA, issue_body="## Why\nEdited issue body after prepare.\n")
        assert SHIP.main(["publication-gate"]) == 0
        assert json.loads(capsys.readouterr().out) == {"outcome": "blocked"}

    def test_publication_gate_marks_incomplete_when_head_sha_advanced_after_review(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _seed_prepared_state(workspace)
        _mock_git_and_issue(monkeypatch, head_sha=HEAD_SHA, issue_body=ISSUE_BODY)
        _write_stage(workspace, "validate", "passed")
        assert SHIP.main(["record", "--action", "validate"]) == 0
        _write_stage(workspace, "review", "ready")
        assert SHIP.main(["record", "--action", "review"]) == 0
        capsys.readouterr()

        _mock_git_and_issue(monkeypatch, head_sha=NEW_HEAD_SHA, issue_body=ISSUE_BODY)
        assert SHIP.main(["publication-gate"]) == 0
        assert json.loads(capsys.readouterr().out) == {"outcome": "incomplete"}


def _seed_permitted_publication(
    root: Path, *, head_sha: str = HEAD_SHA, permitted: bool = True, outcome: str = "ready"
) -> None:
    """Write .agentic/ship/publication.json in root."""
    pub_path = root / ".agentic" / "ship" / "publication.json"
    pub_path.parent.mkdir(parents=True, exist_ok=True)
    pub_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "permitted": permitted,
                "outcome": outcome,
                "head_sha": head_sha,
                "body_sha": ISSUE_BODY_SHA,
                "live_body_sha": ISSUE_BODY_SHA,
                "reason": "Gated",
            }
        ),
        encoding="utf-8",
    )


def _validate_cmd_response(cmd: tuple[str, ...], *, fail_ruff_check: bool) -> subprocess.CompletedProcess[str]:
    """Return a fake CompletedProcess for one command during `ship_state.py validate`."""
    if cmd[:3] == ("git", "rev-parse", "HEAD") or cmd[:2] == ("git", "merge-base"):
        return _completed(cmd, stdout=f"{HEAD_SHA}\n")
    if cmd[:2] in (("git", "diff"), ("git", "ls-files")):
        return _completed(cmd, stdout="")
    if fail_ruff_check and cmd[:4] == ("uv", "run", "ruff", "check"):
        return _completed(cmd, stdout="foo.py:1:1: F401 unused import\n", returncode=1)
    return _completed(cmd, stdout="ok\n", returncode=0)


def _mock_validate_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_ruff_check: bool = False,
    captured_envs: list[dict[str, str]] | None = None,
) -> None:
    """Patch subprocess.run for `ship_state.py validate` tests."""

    def fake_run(
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        check: bool = True,
        capture_output: bool = True,
        text: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, check, capture_output, text
        if env is not None and captured_envs is not None:
            captured_envs.append(dict(env))
        return _validate_cmd_response(tuple(args), fail_ruff_check=fail_ruff_check)

    monkeypatch.setattr(subprocess, "run", fake_run)


def _publish_cmd_response(
    cmd: tuple[str, ...],
    *,
    workspace: Path,
    pr_py_rc: int,
    push_rc: int,
) -> subprocess.CompletedProcess[str]:
    """Return a fake CompletedProcess for one git/gh/uv command during `ship_state.py publish`."""
    if cmd[:3] == ("git", "rev-parse", "HEAD"):
        return _completed(cmd, stdout=f"{HEAD_SHA}\n")
    if cmd[:2] == ("git", "status"):
        return _completed(cmd, stdout="")
    if cmd[:2] == ("git", "push"):
        return _completed(cmd, stderr="network error" if push_rc else "", returncode=push_rc)
    if cmd[:4] == ("uv", "run", "python", "scripts/pr.py"):
        if pr_py_rc == 0:
            (workspace / ".agentic/pr-url").write_text("https://github.com/devexhq/dovo/pull/1372\n", encoding="utf-8")
        return _completed(cmd, stderr="ambiguous failure" if pr_py_rc else "", returncode=pr_py_rc)
    if cmd[:4] == ("uv", "run", "python", "scripts/publish_review.py"):
        return _completed(
            cmd, stdout="Posted review comment: https://github.com/devexhq/dovo/pull/1372#issuecomment-99\n"
        )
    if cmd[:3] in (("gh", "api", "repos/{owner}/{repo}/pulls/1372"), GH_PR_LIST):
        pr_payload = {
            "number": 1372,
            "head": {"ref": "luckner/1367-ship", "sha": HEAD_SHA},
            "base": {"ref": "main"},
            "html_url": "https://github.com/devexhq/dovo/pull/1372",
            "state": "open",
            "body": "Closes #1367",
        }
        return _completed(cmd, stdout=json.dumps(pr_payload) + "\n")
    return _completed(cmd)


class ValidateAndPublishTests:
    def test_validate_runs_gates_with_sanitized_env_and_writes_passed_stage_envelope(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _seed_prepared_state(workspace)
        monkeypatch.setenv("DOVO_STEP_ID", "validate")
        captured_envs: list[dict[str, str]] = []
        _mock_validate_subprocess(monkeypatch, captured_envs=captured_envs)

        assert SHIP.main(["validate"]) == 0
        assert json.loads(capsys.readouterr().out) == {"disposition": "passed", "head_sha": HEAD_SHA}
        stage = _read_json(workspace / ".agentic/ship/stage.json")
        assert stage["stage"] == "validate"
        assert stage["disposition"] == "passed"
        assert stage["body_sha"] == ISSUE_BODY_SHA
        assert stage["head_sha"] == HEAD_SHA
        assert (workspace / ".agentic/ship/validate.md").is_file()
        assert captured_envs
        assert "DOVO_STEP_ID" not in captured_envs[0]
        assert captured_envs[0]["COLUMNS"] == "160"

    def test_validate_records_checks_failed_with_zero_exit_code_when_gate_fails(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _seed_prepared_state(workspace)
        _mock_validate_subprocess(monkeypatch, fail_ruff_check=True)

        assert SHIP.main(["validate"]) == 0
        assert json.loads(capsys.readouterr().out) == {"disposition": "checks_failed", "head_sha": HEAD_SHA}
        stage = _read_json(workspace / ".agentic/ship/stage.json")
        assert stage["disposition"] == "checks_failed"
        assert "ruff check (exit 1)" in str(stage["summary"])
        assert "F401 unused import" in (workspace / ".agentic/ship/validate.md").read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        ("permitted", "pub_head", "dirty_out"),
        [
            pytest.param(False, HEAD_SHA, "", id="unpermitted-publication"),
            pytest.param(True, NEW_HEAD_SHA, "", id="mismatched-head-sha"),
            pytest.param(True, HEAD_SHA, " M src/dovo/app.py\n", id="dirty-worktree"),
        ],
    )
    def test_publish_refuses_when_gate_or_worktree_precondition_fails(
        self,
        permitted: bool,
        pub_head: str,
        dirty_out: str,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _seed_prepared_state(workspace)
        _seed_permitted_publication(workspace, head_sha=pub_head, permitted=permitted)

        def fake_run(
            args: Sequence[str],
            *,
            cwd: Path | None = None,
            check: bool = True,
            capture_output: bool = True,
            text: bool = True,
        ) -> subprocess.CompletedProcess[str]:
            del cwd, check, capture_output, text
            cmd = tuple(args)
            if cmd[:3] == ("git", "rev-parse", "HEAD"):
                return _completed(cmd, stdout=f"{HEAD_SHA}\n")
            return _completed(cmd, stdout=dirty_out)

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert SHIP.main(["publish"]) == 1

    @pytest.mark.parametrize("pr_py_rc", [0, 1], ids=["clean-pr-py", "reconciled-after-ambiguous-pr-py-failure"])
    def test_publish_new_pr_and_reconciles_remote_state_on_ambiguous_failure(
        self, pr_py_rc: int, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _seed_prepared_state(workspace)
        _seed_permitted_publication(workspace, head_sha=HEAD_SHA)
        monkeypatch.setenv("DOVO_SESSION_ID", "dovo_session_1")
        commands_run: list[tuple[str, ...]] = []

        def fake_run(
            args: Sequence[str],
            *,
            cwd: Path | None = None,
            check: bool = True,
            capture_output: bool = True,
            text: bool = True,
        ) -> subprocess.CompletedProcess[str]:
            del cwd, check, capture_output, text
            cmd = tuple(args)
            commands_run.append(cmd)
            return _publish_cmd_response(cmd, workspace=workspace, pr_py_rc=pr_py_rc, push_rc=0)

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert SHIP.main(["publish"]) == 0
        assert json.loads(capsys.readouterr().out) == {
            "pr_number": "1372",
            "url": "https://github.com/devexhq/dovo/pull/1372",
            "head_sha": HEAD_SHA,
        }
        pr_record = _read_json(workspace / ".agentic/ship/pr.json")
        assert pr_record == {
            "pr_number": 1372,
            "url": "https://github.com/devexhq/dovo/pull/1372",
            "branch": "luckner/1367-ship",
            "head_sha": HEAD_SHA,
            "review_comment_url": "https://github.com/devexhq/dovo/pull/1372#issuecomment-99",
        }
        assert (
            "uv",
            "run",
            "python",
            "scripts/publish_review.py",
            "--pr",
            "https://github.com/devexhq/dovo/pull/1372",
            "--session",
            "dovo_session_1",
        ) in commands_run

    def test_publish_existing_pr_pushes_explicit_refspec_and_edits_pr(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_prepared_state(workspace)
        _seed_permitted_publication(workspace, head_sha=HEAD_SHA)
        contract_path = workspace / ".agentic/ship/contract.json"
        contract = _read_json(contract_path)
        contract["pull_request"] = {
            "pr_number": 1372,
            "branch": "luckner/1367-ship",
            "head_sha": HEAD_SHA,
            "base_branch": "main",
            "url": "https://github.com/devexhq/dovo/pull/1372",
        }
        contract_path.write_text(json.dumps(contract), encoding="utf-8")
        (workspace / ".agentic/pr-title").write_text("feat: updated title\n", encoding="utf-8")
        (workspace / ".agentic/pr-description").write_text("Closes #1367\n", encoding="utf-8")
        commands_run: list[tuple[str, ...]] = []

        def fake_run(
            args: Sequence[str],
            *,
            cwd: Path | None = None,
            check: bool = True,
            capture_output: bool = True,
            text: bool = True,
        ) -> subprocess.CompletedProcess[str]:
            del cwd, check, capture_output, text
            cmd = tuple(args)
            commands_run.append(cmd)
            return _publish_cmd_response(cmd, workspace=workspace, pr_py_rc=0, push_rc=0)

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert SHIP.main(["publish"]) == 0
        assert ("git", "push", "origin", "HEAD:refs/heads/luckner/1367-ship") in commands_run
        assert (
            "gh",
            "api",
            "--method",
            "PATCH",
            "repos/{owner}/{repo}/pulls/1372",
            "-f",
            "title=feat: updated title",
            "-f",
            "body=Closes #1367",
            "--silent",
        ) in commands_run


class ShipCommandStepsTests:
    @pytest.mark.parametrize(
        "step_name",
        ["prepare", "next", "record", "validate", "publication-gate", "publish", "report"],
    )
    def test_command_step_yaml_invokes_ship_state_subcommand_with_abort_policy(self, step_name: str) -> None:
        step_path = REPO_ROOT / ".dovo" / "catalog" / "steps" / "ship" / f"{step_name}.yml"
        assert step_path.is_file()
        parsed = yaml.safe_load(step_path.read_text(encoding="utf-8"))
        assert isinstance(parsed, dict)
        assert parsed["id"] == step_name
        assert parsed["type"] == "command"
        assert parsed["on_failure"] == "abort"
        assert parsed["command"] == f"uv run python .dovo/catalog/scripts/sdlc/ship/ship_state.py {step_name}"
