"""Tier 1 contract: sdlc v2's plan.py checks a slice plan's decisions, posts them to the issue and reads them back."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from tests.lint.astlib import REPO_ROOT

SCRIPTS = REPO_ROOT / ".dovo/catalog/scripts/sdlc/v2"


def _load_script(name: str, module_name: str) -> ModuleType:
    """Load sdlc v2's <name>.py by path as module_name, since its folder is not a package."""
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_plan() -> ModuleType:
    """Load plan.py as sdlc_v2_plan, with plan_formats under the bare name it imports only while it loads.

    plan_formats stays registered as sdlc_v2_plan_formats, so another version's copy never collides with it.
    """
    sys.modules["plan_formats"] = _load_script("plan_formats", "sdlc_v2_plan_formats")
    try:
        return _load_script("plan", "sdlc_v2_plan")
    finally:
        del sys.modules["plan_formats"]


PLAN = _load_plan()

HUMAN = {
    "id": "D-3",
    "was": "OQ-4",
    "decision": "Required config's ConfigError carries the load's diagnostic records.",
    "settled_by": "human",
    "answer": "accept challenge",
    "rejected": ["A raise without the records (diagnostics: [])."],
    "phases": [6],
    "date": "2026-10-29",
}
ASSUMED = {
    "id": "D-4",
    "was": "OQ-5",
    "decision": "Discarded sessions stay hidden unless --all is passed.",
    "settled_by": "assumed_accepted",
    "rejected": ["Show them with a discarded status column."],
    "phases": [1],
    "date": "2026-10-29",
}
PLANNER = {
    "id": "D-5",
    "was": None,
    "decision": "The list reuses worktree_list's empty-state line.",
    "settled_by": "planner",
    "rejected": [],
    "phases": [],
    "date": "2026-10-29",
}


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Return an empty working directory with an .agentic folder."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentic").mkdir()
    return tmp_path


def _write_plan(root: Path, open_questions: list[object], decisions: list[object]) -> None:
    """Write a slice plan.json with open_questions and decisions."""
    plan = {"issue": 914, "phases": 2, "open_questions": open_questions, "decisions": decisions}
    (root / ".agentic" / "plan.json").write_text(json.dumps(plan), encoding="utf-8")


def _main(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    """Run plan.py's main with argv."""
    monkeypatch.setattr(sys, "argv", ["plan.py", *argv])
    return PLAN.main()


def _check(monkeypatch: pytest.MonkeyPatch) -> int:
    """Run plan.py check-questions on .agentic/plan.json."""
    return _main(monkeypatch, "check-questions", "--plan", ".agentic/plan.json")


class PlanCheckQuestionsTests:
    def test_check_questions_passes_a_plan_with_every_question_settled(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_plan(workspace, [], [HUMAN, ASSUMED, PLANNER])

        assert _check(monkeypatch) == 0

        assert capsys.readouterr() == ("", "")

    def test_check_questions_passes_a_plan_with_no_decisions(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_plan(workspace, [], [])

        assert _check(monkeypatch) == 0

        assert capsys.readouterr() == ("", "")

    def test_check_questions_reads_the_default_plan_path(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_plan(workspace, [{"id": "OQ-2"}], [])

        assert _main(monkeypatch, "check-questions") == 1

        assert capsys.readouterr().err.startswith("plan.py: .agentic/plan.json still lists open questions OQ-2;")

    def test_check_questions_with_a_slice_refuses_a_decision_naming_a_phase_outside_it(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        slices = [{"id": "S3", "phases": [1, 2]}]
        decisions = [{**HUMAN, "phases": [2, 6]}, ASSUMED, {**PLANNER, "phases": [9]}]
        _write_json(workspace / ".agentic/plan.json", {"open_questions": [], "decisions": decisions, "slices": slices})

        assert _main(monkeypatch, "check-questions", "--slice", "S3") == 1

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == (
            "plan.py: .agentic/plan.json: Decision D-3 names phase 6; Decision D-5 names phase 9, "
            "which slice S3 does not have; its phases are 1, 2.\n"
        )

        _write_json(
            workspace / ".agentic/plan.json", {"open_questions": [], "decisions": [ASSUMED, PLANNER], "slices": slices}
        )

        assert _main(monkeypatch, "check-questions", "--slice", "S3") == 0

    def test_check_questions_refuses_a_question_still_open(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_plan(workspace, [{"id": "OQ-2", "summary": "Hide them? (assumed: yes)"}, {"summary": "No id"}], [HUMAN])

        assert _check(monkeypatch) == 1

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == (
            "plan.py: .agentic/plan.json still lists open questions OQ-2, at position 2; "
            "fold-answers must settle each one as a decision.\n"
        )

    @pytest.mark.parametrize(
        ("decision", "message"),
        [
            pytest.param(
                {key: value for key, value in HUMAN.items() if key != "settled_by"},
                "Decision D-3 has no settled_by.",
                id="no-settled-by",
            ),
            pytest.param(
                {**HUMAN, "settled_by": "reviewer"},
                "Decision D-3 has settled_by 'reviewer'; it must be one of human, assumed_accepted, planner.",
                id="unknown-settled-by",
            ),
            pytest.param(
                {**HUMAN, "id": "OQ-4"},
                "Decision 1 has id 'OQ-4'; it must be D-<n>.",
                id="question-id",
            ),
            pytest.param("D-3", "Decision 1 is not a JSON object.", id="not-an-object"),
            pytest.param({**HUMAN, "decision": " "}, "Decision D-3 has no decision.", id="empty-decision"),
            pytest.param(
                {key: value for key, value in HUMAN.items() if key != "answer"},
                "Decision D-3 has no answer.",
                id="human-without-answer",
            ),
            pytest.param(
                {**ASSUMED, "answer": "yes"},
                "Decision D-4 has an answer, but only a decision settled_by human has one.",
                id="assumed-with-answer",
            ),
            pytest.param(
                {**ASSUMED, "was": None},
                "Decision D-4 has was None; it must be the id of the open question it settles.",
                id="assumed-without-question",
            ),
            pytest.param(
                {**PLANNER, "was": "D-2"},
                "Decision D-5 has was 'D-2'; it must be the id of the open question it settles.",
                id="planner-was-not-a-question",
            ),
            pytest.param(
                {**HUMAN, "rejected": "A raise"},
                "Decision D-3 has no rejected list of strings.",
                id="rejected-not-a-list",
            ),
            pytest.param(
                {**HUMAN, "phases": [True]},
                "Decision D-3 has no phases list of phase numbers.",
                id="phases-not-numbers",
            ),
            pytest.param(
                {**HUMAN, "date": "2026-02-30"},
                "Decision D-3 has date '2026-02-30'; it must be a YYYY-MM-DD date.",
                id="impossible-date",
            ),
            pytest.param(
                {**HUMAN, "date": "20261029"},
                "Decision D-3 has date '20261029'; it must be a YYYY-MM-DD date.",
                id="compact-date",
            ),
        ],
    )
    def test_check_questions_refuses_a_malformed_decision_naming_the_field(
        self,
        decision: object,
        message: str,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_plan(workspace, [], [decision])

        assert _check(monkeypatch) == 1

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == f"plan.py: .agentic/plan.json: {message}\n"

    def test_check_questions_refuses_a_decision_id_used_twice(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_plan(workspace, [], [HUMAN, {**ASSUMED, "id": "D-3"}])

        assert _check(monkeypatch) == 1

        assert capsys.readouterr().err == "plan.py: .agentic/plan.json: Decision D-3 appears twice.\n"

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            pytest.param(
                "not json",
                ".agentic/plan.json cannot be read: Expecting value: line 1 column 1 (char 0).",
                id="not-json",
            ),
            pytest.param("[]", ".agentic/plan.json is not a JSON object.", id="not-an-object"),
            pytest.param('{"decisions": []}', ".agentic/plan.json has no open_questions list.", id="no-open-questions"),
            pytest.param('{"open_questions": []}', ".agentic/plan.json has no decisions list.", id="no-decisions"),
        ],
    )
    def test_check_questions_refuses_an_unreadable_plan(
        self,
        text: str,
        message: str,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        (workspace / ".agentic" / "plan.json").write_text(text, encoding="utf-8")

        assert _check(monkeypatch) == 1

        assert capsys.readouterr() == ("", f"plan.py: {message}\n")

    def test_check_questions_refuses_a_missing_plan(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert _check(monkeypatch) == 1

        assert capsys.readouterr().err.startswith("plan.py: .agentic/plan.json cannot be read: ")


SLICE = "S2"
SESSION = "blueprint_code02"
DESCRIPTION = (
    "Slice S2 of #925.\n\n## Goal / User Story\n\nList discarded sessions.  \n\n## Scope\n\nOnly `dovo session list`.\n"
)
DESCRIPTION_SHA = "584163b1c188761f1b9f70578ae35323dfbbd478c65c7cd6fa30bd28c427eb24"
STALE_SHA = "0" * 64
REPLACED_BULLET = "- **D-1** Sessions are listed oldest first. *Planner, approved with the plan.*"
REPLACING = {
    "id": "D-6",
    "was": "OQ-6",
    "decision": "Sessions are listed\n  newest first.",
    "settled_by": "assumed_accepted",
    "rejected": [],
    "phases": [1],
    "date": "2026-10-30",
    "replaces": {"id": "D-1", "session": "blueprint_plan01"},
}
VIEW = ("gh", "issue", "view", "1300", "--json", "body,comments")
MINIMIZE = (
    "query=mutation($id: ID!) { minimizeComment(input: {subjectId: $id, classifier: OUTDATED}) { clientMutationId } }"
)
POSTED = (
    f"<!-- dovo:decisions schema_version=1 slice=S2 session=blueprint_code02 body_sha={DESCRIPTION_SHA} -->\n"
    "**Planning decisions** (2026-10-30, slice S2, session blueprint_code02)\n"
    "\n"
    "- **D-3** Required config's ConfigError carries the load's diagnostic records. *Answered: accept challenge.* "
    "Rejected: A raise without the records (diagnostics: []).\n"
    "- **D-4** Discarded sessions stay hidden unless --all is passed. *Assumed answer, accepted.* "
    "Rejected: Show them with a discarded status column.\n"
    "- **D-5** The list reuses worktree_list's empty-state line. *Planner, approved with the plan.*\n"
    "- **D-6** Sessions are listed newest first. *Assumed answer, accepted.* "
    "Replaces D-1 (session blueprint_plan01).\n"
)


def _comment(node_id: str, session: str, sha: str, *bullets: str, minimized: bool = False) -> dict[str, object]:
    """Return a gh issue view comment holding session's dovo:decisions comment for slice S1."""
    marker = f"<!-- dovo:decisions schema_version=1 slice=S1 session={session} body_sha={sha} -->"
    body = "\n".join([marker, f"**Planning decisions** (2026-10-28, slice S1, session {session})", "", *bullets])
    return {"id": node_id, "body": body, "isMinimized": minimized}


class FakeGh:
    """A `run` double answering the gh calls plan.py makes, against one issue's description and comments.

    It has `run`'s signature, so it replaces it at the subprocess boundary. `calls` records every call,
    with a posted comment's temporary file replaced by its text; `failing` names the calls that fail by
    their third word (`view`, `comment` or `graphql`); `view`, when given, is what `gh issue view` prints
    in place of the issue's JSON, which otherwise holds the requested fields of body, comments and labels.
    """

    def __init__(
        self,
        body: str,
        comments: list[object],
        failing: frozenset[str] = frozenset(),
        view: str | None = None,
        labels: tuple[str, ...] = (),
    ) -> None:
        self.body = body
        self.comments = comments
        self.failing = failing
        self.view = view
        self.labels = labels
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str) -> str:
        kind = args[2]
        assert kind in {"view", "comment", "graphql"}, f"ran {args}"
        self.calls.append((*args[:5], Path(args[5]).read_text(encoding="utf-8")) if kind == "comment" else args)
        if kind in self.failing:
            raise subprocess.CalledProcessError(1, list(args), stderr="HTTP 502\n")
        if kind == "view":
            return self.view if self.view is not None else self._issue(args[5])
        return ""

    def _issue(self, fields: str) -> str:
        issue = {"body": self.body, "comments": self.comments, "labels": [{"name": name} for name in self.labels]}
        return json.dumps({field: issue[field] for field in fields.split(",")})


def _run(monkeypatch: pytest.MonkeyPatch, fake: FakeGh, *argv: str) -> int:
    """Run plan.py's main with argv against fake."""
    monkeypatch.setattr(PLAN, "run", fake)
    return _main(monkeypatch, *argv)


def _post(monkeypatch: pytest.MonkeyPatch, fake: FakeGh, session: str = SESSION, slice_id: str = SLICE) -> int:
    """Run plan.py decisions-comment for slice_id of issue 1300 against fake."""
    argv = ["decisions-comment", "--plan", ".agentic/plan.json", "--issue", "1300", "--slice", slice_id]
    return _run(monkeypatch, fake, *argv, "--session", session)


def _write_prior(root: Path, sha: str = DESCRIPTION_SHA) -> None:
    """Write the .agentic/prior-decisions.json prior-decisions leaves, recording the description's sha."""
    prior = {"schema_version": 1, "issue": 1300, "body_sha": sha, "decisions": []}
    (root / ".agentic" / "prior-decisions.json").write_text(json.dumps(prior), encoding="utf-8")


class PlanBodyShaTests:
    def test_body_sha_hashes_a_fixed_description(self) -> None:
        assert PLAN.body_sha(DESCRIPTION) == DESCRIPTION_SHA

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param(DESCRIPTION.replace("\n", "\r\n") + "\r\n\r\n", id="crlf-and-trailing-newlines"),
            pytest.param(DESCRIPTION.replace("#925.", "#925.   "), id="trailing-spaces"),
        ],
    )
    def test_body_sha_ignores_line_endings_and_trailing_whitespace(self, body: str) -> None:
        assert PLAN.body_sha(body) == DESCRIPTION_SHA

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param(DESCRIPTION.replace("Only", "Just"), id="edited"),
            pytest.param(DESCRIPTION + "\n## Work plan\n\n| S1 | merged |\n", id="work-plan-section-added"),
        ],
    )
    def test_body_sha_changes_with_any_change_to_the_description(self, body: str) -> None:
        assert PLAN.body_sha(body) != DESCRIPTION_SHA


class PlanDecisionsCommentTests:
    def test_decisions_comment_posts_every_decision_on_one_line_each(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_plan(workspace, [], [HUMAN, ASSUMED, PLANNER, REPLACING])
        _write_prior(workspace)
        fake = FakeGh(DESCRIPTION, [])

        assert _post(monkeypatch, fake) == 0

        assert fake.calls == [VIEW, ("gh", "issue", "comment", "1300", "--body-file", POSTED)]
        assert capsys.readouterr() == ("", "")

    def test_decisions_comment_names_the_description_prior_decisions_read(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_plan(workspace, [], [HUMAN, ASSUMED, PLANNER, REPLACING])
        fake = FakeGh(DESCRIPTION, [])
        assert _run(monkeypatch, fake, "prior-decisions", "--issue", "1300") == 0
        fake.body = DESCRIPTION.replace("Only", "Just")

        assert _post(monkeypatch, fake) == 0

        assert fake.calls[-1] == ("gh", "issue", "comment", "1300", "--body-file", POSTED)
        assert capsys.readouterr() == ("", "")

    def test_decisions_comment_refuses_without_a_recorded_body_sha(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_plan(workspace, [], [HUMAN])
        _write_json(workspace / ".agentic/prior-decisions.json", {"schema_version": 1, "issue": 1300, "decisions": []})
        fake = FakeGh(DESCRIPTION, [])

        assert _post(monkeypatch, fake) == 1

        assert fake.calls == []
        assert capsys.readouterr() == (
            "",
            "plan.py: .agentic/prior-decisions.json has no body_sha; run prior-decisions before planning.\n",
        )

    def test_decisions_comment_refuses_a_slice_that_is_not_one_token(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_plan(workspace, [], [HUMAN])
        _write_prior(workspace)
        fake = FakeGh(DESCRIPTION, [])

        assert _post(monkeypatch, fake, slice_id="S 2") == 1

        assert fake.calls == []
        assert capsys.readouterr() == (
            "",
            "plan.py: Slice 'S 2' must be non-empty, without whitespace or parentheses.\n",
        )

    def test_decisions_comment_posts_nothing_for_a_plan_without_decisions(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_plan(workspace, [], [])
        fake = FakeGh(DESCRIPTION, [])

        assert _post(monkeypatch, fake) == 0

        assert fake.calls == []
        assert capsys.readouterr() == ("", "")

    def test_decisions_comment_hides_only_earlier_comments_it_wholly_replaces(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_plan(workspace, [], [PLANNER, REPLACING])
        _write_prior(workspace)
        kept = "- **D-2** Keep the table. *Planner, approved with the plan.*"
        fake = FakeGh(
            DESCRIPTION,
            [
                _comment("IC_replaced", "blueprint_plan01", DESCRIPTION_SHA, REPLACED_BULLET),
                _comment("IC_partly", "blueprint_plan01", DESCRIPTION_SHA, REPLACED_BULLET, kept),
                _comment("IC_other", "blueprint_plan00", DESCRIPTION_SHA, REPLACED_BULLET),
                _comment("IC_hidden", "blueprint_plan01", DESCRIPTION_SHA, REPLACED_BULLET, minimized=True),
                {"id": "IC_chat", "body": "Looks good.", "isMinimized": False},
            ],
        )

        assert _post(monkeypatch, fake) == 0

        assert fake.calls[2:] == [("gh", "api", "graphql", "-f", MINIMIZE, "-f", "id=IC_replaced")]
        assert capsys.readouterr() == ("", "")

    def test_decisions_comment_reports_a_failed_hide_without_failing(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_plan(workspace, [], [REPLACING])
        _write_prior(workspace)
        comments: list[object] = [_comment("IC_replaced", "blueprint_plan01", STALE_SHA, REPLACED_BULLET)]
        fake = FakeGh(DESCRIPTION, comments, frozenset({"graphql"}))

        assert _post(monkeypatch, fake) == 0

        assert [call[:3] for call in fake.calls] == [VIEW[:3], ("gh", "issue", "comment"), ("gh", "api", "graphql")]
        assert capsys.readouterr() == (
            "",
            "plan.py: could not hide comment IC_replaced of session blueprint_plan01: HTTP 502\n",
        )

    def test_decisions_comment_refuses_when_the_post_fails(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_plan(workspace, [], [REPLACING])
        _write_prior(workspace)
        comments: list[object] = [_comment("IC_replaced", "blueprint_plan01", STALE_SHA, REPLACED_BULLET)]
        fake = FakeGh(DESCRIPTION, comments, frozenset({"comment"}))

        assert _post(monkeypatch, fake) == 1

        assert [call[:3] for call in fake.calls] == [VIEW[:3], ("gh", "issue", "comment")]
        assert capsys.readouterr().err.startswith("plan.py: command failed: gh issue comment 1300 --body-file ")

    def test_decisions_comment_refuses_an_empty_session(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_plan(workspace, [], [HUMAN])
        fake = FakeGh(DESCRIPTION, [])

        assert _post(monkeypatch, fake, session="") == 1

        assert fake.calls == []
        assert capsys.readouterr() == (
            "",
            "plan.py: Session '' must be non-empty, without whitespace or parentheses.\n",
        )

    @pytest.mark.parametrize(
        ("replaces", "message"),
        [
            pytest.param({"id": "D-1"}, "its session must be a session id", id="no-session"),
            pytest.param({"id": "OQ-1", "session": "s"}, "its id must be D-<n>", id="question-id"),
            pytest.param("D-1", "its id must be D-<n>", id="not-an-object"),
        ],
    )
    def test_decisions_comment_refuses_a_malformed_replaces(
        self,
        replaces: object,
        message: str,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_plan(workspace, [], [{**REPLACING, "replaces": replaces}])
        fake = FakeGh(DESCRIPTION, [])

        assert _post(monkeypatch, fake) == 1

        assert fake.calls == []
        assert (
            capsys.readouterr().err
            == f"plan.py: .agentic/plan.json: Decision D-6 has replaces {replaces!r}; {message}.\n"
        )


class PlanPriorDecisionsTests:
    def test_prior_decisions_marks_each_decision_given_or_to_confirm(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        replacing = (
            "- **D-3** Hide discarded and archived sessions. *Answered: also archived.* "
            "Replaces D-2 (session blueprint_plan01)."
        )
        fake = FakeGh(
            DESCRIPTION.replace("\n", "\r\n"),
            [
                _comment(
                    "IC_given",
                    "blueprint_plan01",
                    DESCRIPTION_SHA,
                    "- **D-1** Keep the table. *Planner, approved with the plan.*",
                    "- **D-2** Hide discarded sessions. *Assumed answer, accepted.*",
                ),
                {"id": "IC_chat", "body": "Looks good.\n- **D-9** not a decision", "isMinimized": False},
                _comment("IC_stale", "blueprint_plan00", STALE_SHA, "- **D-1** Show them. *Answered: show them.*"),
                _comment("IC_hidden", "blueprint_old", STALE_SHA, REPLACED_BULLET, minimized=True),
                _comment("IC_new", "blueprint_code02", DESCRIPTION_SHA, replacing),
            ],
        )

        assert _run(monkeypatch, fake, "prior-decisions", "--issue", "1300", "--out", ".agentic/prior.json") == 0

        assert fake.calls == [VIEW]
        assert json.loads((workspace / ".agentic" / "prior.json").read_text(encoding="utf-8")) == {
            "schema_version": 1,
            "issue": 1300,
            "body_sha": DESCRIPTION_SHA,
            "decisions": [
                {
                    "id": "D-1",
                    "text": "Keep the table. *Planner, approved with the plan.*",
                    "slice": "S1",
                    "session": "blueprint_plan01",
                    "status": "given",
                },
                {
                    "id": "D-1",
                    "text": "Show them. *Answered: show them.*",
                    "slice": "S1",
                    "session": "blueprint_plan00",
                    "status": "to_confirm",
                },
                {
                    "id": "D-3",
                    "text": replacing.removeprefix("- **D-3** "),
                    "slice": "S1",
                    "session": "blueprint_code02",
                    "status": "given",
                },
            ],
        }
        assert capsys.readouterr() == ("", "")

    def test_prior_decisions_writes_an_empty_list_to_the_default_path(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGh(DESCRIPTION, [])

        assert _run(monkeypatch, fake, "prior-decisions", "--issue", "1300") == 0

        written = json.loads((workspace / ".agentic" / "prior-decisions.json").read_text(encoding="utf-8"))
        assert written == {"schema_version": 1, "issue": 1300, "body_sha": DESCRIPTION_SHA, "decisions": []}

    def test_prior_decisions_keeps_out_what_a_hidden_comment_replaced(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        kept = "- **D-2** Keep the table. *Planner, approved with the plan.*"
        middle = "- **D-1** Show them. *Answered: show them.* Replaces D-1 (session blueprint_plan00)."
        latest = "- **D-1** Hide them. *Answered: hide them.* Replaces D-1 (session blueprint_plan01)."
        fake = FakeGh(
            DESCRIPTION,
            [
                _comment("IC_first", "blueprint_plan00", DESCRIPTION_SHA, REPLACED_BULLET, kept),
                _comment("IC_middle", "blueprint_plan01", DESCRIPTION_SHA, middle, minimized=True),
                _comment("IC_latest", "blueprint_code02", DESCRIPTION_SHA, latest),
            ],
        )

        assert _run(monkeypatch, fake, "prior-decisions", "--issue", "1300") == 0

        written = _written(workspace, "prior-decisions.json")["decisions"]
        assert isinstance(written, list)
        assert [(entry["id"], entry["session"]) for entry in written] == [
            ("D-2", "blueprint_plan00"),
            ("D-1", "blueprint_code02"),
        ]

    @pytest.mark.parametrize(
        ("printed", "message"),
        [
            pytest.param("not json", "printed no JSON: Expecting value: line 1 column 1 (char 0).", id="not-json"),
            pytest.param("[]", "printed no JSON object.", id="not-an-object"),
        ],
    )
    @pytest.mark.parametrize(("command", "fields"), [("prior-decisions", "body,comments"), ("requirements", "body")])
    def test_prior_decisions_and_requirements_refuse_when_gh_prints_no_json_object(
        self,
        command: str,
        fields: str,
        printed: str,
        message: str,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake = FakeGh(DESCRIPTION, [], view=printed)

        assert _run(monkeypatch, fake, command, "--issue", "1300", "--out", ".agentic/out.json") == 1

        assert fake.calls == [("gh", "issue", "view", "1300", "--json", fields)]
        assert capsys.readouterr() == ("", f"plan.py: gh issue view 1300 {message}\n")
        assert not (workspace / ".agentic" / "out.json").exists()

    def test_prior_decisions_refuses_when_gh_fails(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh(DESCRIPTION, [], frozenset({"view"}))

        assert _run(monkeypatch, fake, "prior-decisions", "--issue", "1300") == 1

        assert capsys.readouterr() == (
            "",
            "plan.py: command failed: gh issue view 1300 --json body,comments: HTTP 502\n",
        )
        assert not (workspace / ".agentic" / "prior-decisions.json").exists()


SHORT_BODY = """Slice S1 of #1173.

## Why
Git becomes the only store of worktree state, so the table only receives writes nobody reads.

## Depends on
- #1174: nothing reads the table after it; if planning finds a reader, stop and raise it.

## Outcomes
- **O-1** A fresh `dovo init` database has no worktrees table.
- **O-2** `dovo worktree create` and `dovo worktree delete` work without the table
  and never recreate it.

```markdown
- **O-9** A quoted example, never an outcome.
```

## Decisions
- **D-1** Downgrading recreates the table empty; rows are not rebuilt from Git. Accepted.
- **O-3** An outcome under Decisions is not read.

## Out of scope
- **D-2** A decision outside Decisions is not read.

## Done when
O-1 and O-2 hold.
"""
LEGACY_BODY = """## Goal / User Story

**As a** user, **I want** the table dropped, **so that** Git is the only store.

## Functional requirements

### FR-1: Drop the table
A fresh database has no `worktrees` table.

```text
### FR-9: quoted, never a requirement
```

### FR-2: Commands work without it

## Non-functional requirements

### NFR-1: Reversible
The migration has a downgrade.

## Error cases to handle

1. A database already without the table → the upgrade is a no-op,
   exit 0.
2. A locked database → `LockTimeoutError` propagates.

## Definition of done

1. Not an error case.
"""


def _requirements(monkeypatch: pytest.MonkeyPatch, fake: FakeGh, *argv: str) -> int:
    """Run plan.py requirements for issue 1300 against fake."""
    return _run(monkeypatch, fake, "requirements", "--issue", "1300", *argv)


def _written(workspace: Path, name: str = "requirements.json") -> dict[str, object]:
    """Return the parsed .agentic/<name>."""
    written = json.loads((workspace / ".agentic" / name).read_text(encoding="utf-8"))
    assert isinstance(written, dict)
    return written


class PlanRequirementsTests:
    def test_requirements_lists_a_short_issues_outcomes_and_decisions(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh(SHORT_BODY, [])

        assert _requirements(monkeypatch, fake, "--out", ".agentic/req.json") == 0

        assert fake.calls == [("gh", "issue", "view", "1300", "--json", "body")]
        assert _written(workspace, "req.json") == {
            "schema_version": 1,
            "issue": 1300,
            "format": "short",
            "requirements": [
                {"id": "O-1", "kind": "outcome", "text": "A fresh `dovo init` database has no worktrees table."},
                {
                    "id": "O-2",
                    "kind": "outcome",
                    "text": "`dovo worktree create` and `dovo worktree delete` work without the table and never "
                    "recreate it.",
                },
                {
                    "id": "D-1",
                    "kind": "decision",
                    "text": "Downgrading recreates the table empty; rows are not rebuilt from Git. Accepted.",
                },
            ],
        }
        assert capsys.readouterr() == ("", "")

    def test_requirements_lists_a_legacy_issues_requirements_and_error_cases(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh(LEGACY_BODY.replace("\n", "\r\n"), [])

        assert _requirements(monkeypatch, fake) == 0

        assert _written(workspace) == {
            "schema_version": 1,
            "issue": 1300,
            "format": "legacy",
            "requirements": [
                {
                    "id": "FR-1",
                    "kind": "functional",
                    "text": "Drop the table\n\nA fresh database has no `worktrees` table.\n\n"
                    "```text\n### FR-9: quoted, never a requirement\n```",
                },
                {"id": "FR-2", "kind": "functional", "text": "Commands work without it"},
                {"id": "NFR-1", "kind": "non_functional", "text": "Reversible\n\nThe migration has a downgrade."},
                {
                    "id": "EC-1",
                    "kind": "error_case",
                    "text": "A database already without the table → the upgrade is a no-op, exit 0.",
                },
                {"id": "EC-2", "kind": "error_case", "text": "A locked database → `LockTimeoutError` propagates."},
            ],
        }
        assert capsys.readouterr() == ("", "")

    def test_requirements_reads_the_issue_templates_skeleton_as_a_short_issue(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        template = (REPO_ROOT / ".dovo/catalog/prompts/sdlc/v2/templates/issue.md").read_text(encoding="utf-8")
        skeleton = template.split("```markdown\n", 1)[1].split("\n```", 1)[0]
        fake = FakeGh(skeleton, [])

        assert _requirements(monkeypatch, fake) == 0

        written = _written(workspace)
        assert written["format"] == "short"
        assert written["requirements"] == [
            {
                "id": "O-1",
                "kind": "outcome",
                "text": "<Behaviour a user or caller can observe once the issue is done: what they run, see or get.>",
            },
            {"id": "D-1", "kind": "decision", "text": "<A trade-off a person settled, and what it costs. Accepted.>"},
        ]

    @pytest.mark.parametrize(
        ("body", "ident"),
        [
            pytest.param(SHORT_BODY.replace("- **O-1**", "- **O-2**"), "O-2", id="outcome"),
            pytest.param(LEGACY_BODY.replace("### NFR-1", "### FR-2"), "FR-2", id="functional"),
        ],
    )
    def test_requirements_refuses_an_id_numbered_twice(
        self,
        body: str,
        ident: str,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake = FakeGh(body, [])

        assert _requirements(monkeypatch, fake) == 1

        assert capsys.readouterr() == ("", f"plan.py: Issue 1300 numbers {ident} twice; give each item its own id.\n")
        assert not (workspace / ".agentic" / "requirements.json").exists()

    def test_requirements_numbers_error_cases_by_position(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGh(LEGACY_BODY.replace("2. A locked", "1. A locked"), [])

        assert _requirements(monkeypatch, fake) == 0

        written = _written(workspace)["requirements"]
        assert isinstance(written, list)
        assert [entry["id"] for entry in written if entry["kind"] == "error_case"] == ["EC-1", "EC-2"]
        assert written[-1]["text"] == "A locked database → `LockTimeoutError` propagates."

    @pytest.mark.parametrize(
        ("body", "issue_format"),
        [
            pytest.param("## Why\nNo outcomes yet.\n\n## Outcomes\n- O-1 without bold.\n", "short", id="short"),
            pytest.param("## Goal / User Story\n\nJust a goal.\n", "legacy", id="legacy"),
            pytest.param("", "legacy", id="empty"),
        ],
    )
    def test_requirements_writes_an_empty_list_when_nothing_is_numbered(
        self, body: str, issue_format: str, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGh(body, [])

        assert _requirements(monkeypatch, fake) == 0

        assert _written(workspace) == {"schema_version": 1, "issue": 1300, "format": issue_format, "requirements": []}

    def test_requirements_refuses_when_gh_fails(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh(SHORT_BODY, [], frozenset({"view"}))

        assert _requirements(monkeypatch, fake) == 1

        assert capsys.readouterr() == ("", "plan.py: command failed: gh issue view 1300 --json body: HTTP 502\n")
        assert not (workspace / ".agentic" / "requirements.json").exists()

    def test_requirements_for_a_slice_of_the_plans_issue_lists_only_the_outcomes_it_delivers(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        slices = [{"id": "S1", "outcomes": ["O-1", "O-2 (create)"]}, {"id": "S2", "outcomes": ["O-2 (delete)"]}]
        _write_json(workspace / ".agentic/epic-plan.json", {"issue": 1300, "slices": slices})

        assert (
            _requirements(monkeypatch, FakeGh(SHORT_BODY, []), "--slice", "S2", "--plan", ".agentic/epic-plan.json")
            == 0
        )

        assert [item["id"] for item in _requirement_items(workspace)] == ["O-2"]
        assert capsys.readouterr() == ("", "")

    @pytest.mark.parametrize(
        ("issue", "slices"),
        [
            pytest.param(1173, [{"id": "S1", "outcomes": ["O-4"]}, {"id": "S2", "outcomes": ["O-5"]}], id="own-issue"),
            pytest.param(1300, [{"id": "S2", "outcomes": ["O-1"]}], id="one-slice"),
        ],
    )
    def test_requirements_for_a_slice_keeps_every_item_of_its_own_issue_or_a_one_slice_plan(
        self, issue: int, slices: list[object], workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_json(workspace / ".agentic/plan.json", {"issue": issue, "slices": slices})

        assert _requirements(monkeypatch, FakeGh(SHORT_BODY, []), "--slice", "S2") == 0

        assert [item["id"] for item in _requirement_items(workspace)] == ["O-1", "O-2", "D-1"]

    def test_requirements_refuses_a_slice_the_plan_does_not_have(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        slices = [{"id": "S1", "outcomes": ["O-1"]}, {"id": "S2", "outcomes": ["O-2"]}]
        _write_json(workspace / ".agentic/plan.json", {"issue": 1300, "slices": slices})

        assert _requirements(monkeypatch, FakeGh(SHORT_BODY, []), "--slice", "S9") == 1

        assert capsys.readouterr() == ("", "plan.py: .agentic/plan.json has no slice S9.\n")
        assert not (workspace / ".agentic" / "requirements.json").exists()


def _requirement_items(workspace: Path) -> list[dict[str, object]]:
    """Return the requirements .agentic/requirements.json lists."""
    items = _written(workspace)["requirements"]
    assert isinstance(items, list)
    return items


# --- ready-comment and ready-state -----------------------------------------------

READY_TEXT = "Ready to plan at 630086c0 (2026-10-30).\nNo changes needed.\n"


def _ready(sha: str, *, hidden: bool = False) -> dict[str, object]:
    """Return a dovo:ready comment naming sha, hidden when asked."""
    return {"body": f"<!-- dovo:ready schema_version=1 body_sha={sha} -->\n{READY_TEXT}", "isMinimized": hidden}


class PlanReadyTests:
    def test_ready_comment_posts_the_comment_headed_by_the_descriptions_sha(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (workspace / ".agentic/issue-ready-comment.md").write_text(READY_TEXT, encoding="utf-8")
        fake = FakeGh(DESCRIPTION, [])

        argv = ("ready-comment", "--issue", "1300", "--comment", ".agentic/issue-ready-comment.md")
        assert _run(monkeypatch, fake, *argv) == 0

        assert fake.calls == [
            ("gh", "issue", "view", "1300", "--json", "body"),
            ("gh", "issue", "comment", "1300", "--body-file", str(_ready(DESCRIPTION_SHA)["body"])),
        ]
        assert capsys.readouterr() == ("", "")

    def test_ready_comment_refuses_an_empty_comment(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (workspace / ".agentic/issue-ready-comment.md").write_text("\n", encoding="utf-8")
        fake = FakeGh(DESCRIPTION, [])

        argv = ("ready-comment", "--issue", "1300", "--comment", ".agentic/issue-ready-comment.md")
        assert _run(monkeypatch, fake, *argv) == 1

        assert capsys.readouterr() == (
            "",
            "plan.py: .agentic/issue-ready-comment.md is empty; the ready check writes the comment.\n",
        )
        assert fake.calls == []

    @pytest.mark.parametrize(
        ("labels", "comments", "out", "reason"),
        [
            pytest.param(
                (), [_ready(DESCRIPTION_SHA)], "ready=false", "Issue 1300 is not labelled dovo:ready; checking it.",
                id="not-labelled",
            ),
            pytest.param(
                ("dovo:ready",), [{"body": READY_TEXT, "isMinimized": False}], "ready=false",
                "No ready comment on issue 1300 names the description it checked; checking it.",
                id="no-marker",
            ),
            pytest.param(
                ("dovo:ready",), [_ready(DESCRIPTION_SHA), _ready(STALE_SHA)], "ready=false",
                "Issue 1300's description changed since the check that labelled it; checking it.",
                id="description-changed",
            ),
            pytest.param(
                ("dovo-size-s", "dovo:ready"), [_ready(STALE_SHA), _ready(DESCRIPTION_SHA), _ready(STALE_SHA, hidden=True)],
                "ready=true", "Issue 1300 is labelled dovo:ready for its description as it is; not checking it.",
                id="unchanged",
            ),
        ],
    )  # fmt: skip
    def test_ready_state_is_true_only_for_the_description_the_labelling_check_read(
        self,
        labels: tuple[str, ...],
        comments: list[object],
        out: str,
        reason: str,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake = FakeGh(DESCRIPTION, comments, labels=labels)

        assert _run(monkeypatch, fake, "ready-state", "--issue", "1300") == 0

        assert capsys.readouterr() == (f"{out}\n", f"plan.py: {reason}\n")
        assert fake.calls == [("gh", "issue", "view", "1300", "--json", "body,labels,comments")]


# --- check-breakdown -------------------------------------------------------------

O_1 = "`dovo session list` hides discarded sessions."
O_2 = "`dovo session list --all` shows them with a discarded status."
ISSUE_D_1 = "Discarded is a status, not a separate list."
REQUIREMENTS = {
    "schema_version": 1,
    "issue": 1300,
    "format": "short",
    "requirements": [
        {"id": "O-1", "kind": "outcome", "text": O_1},
        {"id": "O-2", "kind": "outcome", "text": O_2},
        {"id": "D-1", "kind": "decision", "text": ISSUE_D_1},
    ],
}
PHASE_1 = {
    "schema_version": 1,
    "slice": "S3",
    "phase": 1,
    "title": "Hide discarded sessions",
    "depends_on": [],
    "intent": "The list filters on the session's status before it renders.",
    "requirements": [{"id": "O-1", "text": O_1}, {"id": "D-1", "text": ISSUE_D_1}],
    "decisions": ["D-4"],
    "traps": ["The JSON output filters too."],
    "builds_on": [{"symbol": "SessionStatus", "path": "src/dovo/core/sessions/models.py"}],
    "binding": ["class SessionListResult(BaseResult):\n    hidden: int"],
    "departures": ["**D-4** Filter in the core, not the formatter."],
    "tests": ["`tests/cli/session/test_list.py`: a discarded session is not listed (O-1)."],
    "owned_elsewhere": ["#1197 owns deleting SessionStatus.", "#1228 owns the artifact enums."],
    "actions": [
        {
            "id": "1.1",
            "kind": "code",
            "target": "src/dovo/core/sessions/sessions.py::Sessions.list",
            "do": "Leave out discarded sessions.",
            "requirements": ["O-1", "D-1"],
        },
        {
            "id": "1.2",
            "kind": "test",
            "target": "tests/cli/session/test_list.py",
            "do": "A discarded session is not listed.",
            "requirements": ["O-1"],
        },
    ],
    "not_this_phase": ["Phase 2: the --all flag."],
}
PHASE_2 = {
    **PHASE_1,
    "phase": 2,
    "title": "Show them with --all",
    "depends_on": [1],
    "requirements": [{"id": "O-2", "text": O_2}],
    "decisions": ["D-5"],
    "builds_on": [],
    "binding": [],
    "departures": [],
    "tests": [],
    "actions": [
        {
            "id": "2.1",
            "kind": "code",
            "target": "src/dovo/cli/session/commands/list.py",
            "do": "Bind --all.",
            "requirements": ["O-2"],
        },
        {"id": "2.2", "kind": "doc", "target": "docs/cli/session.md", "do": "Document --all.", "requirements": []},
    ],
    "not_this_phase": [],
}
PHASE_3 = {**PHASE_2, "phase": 3, "decisions": [], "actions": [{**PHASE_2["actions"][1], "id": "3.1"}]}
BREAKDOWN_DECISIONS = [{**ASSUMED, "phases": [1]}, {**PLANNER, "phases": [2]}]
BREAKDOWN_SLICES = [{"id": "S2", "phases": []}, {"id": "S3", "phases": [1, 2]}]
PLAN_MD = """# Slice S3 plan

## Owned elsewhere

- #1197 owns deleting
  SessionStatus.
- #1228 owns the artifact enums.

## Phase 1: Hide discarded sessions

### Code

```python
class SessionListResult(BaseResult):
    hidden: int
```

### Decisions

- **D-4** Filter in the core,
  not the formatter.

### Tests

- `tests/cli/session/test_list.py`: a discarded session is not listed (O-1).

```text
- Not binding, and not a test line.
```

## Phase 2: Show them with --all

No new surface.
"""


@pytest.fixture
def breakdown(workspace: Path) -> Path:
    """Return a workspace whose slice S3 has phases 1 and 2, each broken down into a well-formed phase file."""
    _write_json(
        workspace / ".agentic/plan.json",
        {"open_questions": [], "decisions": BREAKDOWN_DECISIONS, "slices": BREAKDOWN_SLICES},
    )
    (workspace / ".agentic/plan.md").write_text(PLAN_MD, encoding="utf-8")
    _write_json(workspace / ".agentic/requirements.json", REQUIREMENTS)
    (workspace / ".agentic/phases").mkdir()
    _write_phase(workspace, 1, PHASE_1)
    _write_phase(workspace, 2, PHASE_2)
    return workspace


def _write_json(path: Path, document: object) -> None:
    """Write document as JSON to path."""
    path.write_text(json.dumps(document), encoding="utf-8")


def _write_phase(root: Path, n: int, document: object) -> None:
    """Write document as .agentic/phases/phase-<n>.json."""
    _write_json(root / f".agentic/phases/phase-{n}.json", document)


def _check_breakdown(monkeypatch: pytest.MonkeyPatch) -> int:
    """Run plan.py check-breakdown for slice S3 on the default paths."""
    return _main(monkeypatch, "check-breakdown", "--slice", "S3")


def _refusal(capsys: pytest.CaptureFixture[str]) -> str:
    """Return the one stderr line of a refusal, asserting nothing went to stdout."""
    captured = capsys.readouterr()
    assert captured.out == ""
    return captured.err


class PlanCheckBreakdownTests:
    def test_check_breakdown_passes_and_writes_the_index(
        self, breakdown: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert _check_breakdown(monkeypatch) == 0

        assert capsys.readouterr() == ("", "")
        assert json.loads((breakdown / ".agentic/phases/index.json").read_text(encoding="utf-8")) == {
            "schema_version": 1,
            "slice": "S3",
            "phases": [
                {
                    "phase": 1,
                    "file": ".agentic/phases/phase-1.json",
                    "title": "Hide discarded sessions",
                    "depends_on": [],
                    "requirements": ["O-1", "D-1"],
                    "decisions": ["D-4"],
                    "targets": ["src/dovo/core/sessions/sessions.py::Sessions.list", "tests/cli/session/test_list.py"],
                },
                {
                    "phase": 2,
                    "file": ".agentic/phases/phase-2.json",
                    "title": "Show them with --all",
                    "depends_on": [1],
                    "requirements": ["O-2"],
                    "decisions": ["D-5"],
                    "targets": ["src/dovo/cli/session/commands/list.py", "docs/cli/session.md"],
                },
            ],
        }

    @pytest.mark.parametrize(
        ("phase", "message"),
        [
            pytest.param(
                {**PHASE_1, "data": [".agentic/pre_determined_data/codes.py"]},
                "phase-1.json has unknown field data.",
                id="unknown-field",
            ),
            pytest.param(
                {**PHASE_1, "actions": [{**PHASE_1["actions"][0], "sketch": "```python\n```"}]},
                "phase-1.json action has unknown field sketch.",
                id="action-sketch",
            ),
            pytest.param(
                {**PHASE_1, "schema_version": 2}, "phase-1.json has schema_version 2; it must be 1.", id="schema"
            ),
            pytest.param({**PHASE_1, "intent": ""}, "phase-1.json has no intent.", id="no-intent"),
            pytest.param({**PHASE_1, "actions": []}, "phase-1.json has no actions.", id="no-actions"),
            pytest.param(
                {**PHASE_1, "actions": [{**PHASE_1["actions"][0], "kind": "refactor"}]},
                "phase-1.json action 1.1 has kind 'refactor'; it must be one of code, test, delete, doc.",
                id="kind",
            ),
            pytest.param(
                {**PHASE_1, "actions": [{**PHASE_1["actions"][0], "id": "2.1"}]},
                "phase-1.json has action id '2.1'; it must be 1.<k>.",
                id="action-id",
            ),
            pytest.param(
                {**PHASE_1, "actions": [{**PHASE_1["actions"][0], "requirements": ["O-2"]}]},
                "phase-1.json action 1.1 cites O-2, which it does not quote.",
                id="unquoted",
            ),
            pytest.param(
                {**PHASE_1, "decisions": ["OQ-4"]},
                "phase-1.json has decisions OQ-4; each must be a D-<n> id from plan.json.",
                id="decision-id",
            ),
            pytest.param(
                PHASE_2,
                "phase-1.json holds phase 2 of slice S3; it must hold phase 1 of slice S3.",
                id="wrong-phase",
            ),
            pytest.param(
                {**PHASE_1, "slice": "S2"},
                "phase-1.json holds phase 1 of slice S2; it must hold phase 1 of slice S3.",
                id="wrong-slice",
            ),
            pytest.param(
                {**PHASE_1, "requirements": [PHASE_1["requirements"][0], *PHASE_1["requirements"]]},
                "phase-1.json lists requirement O-1 twice.",
                id="requirement-twice",
            ),
            pytest.param(
                {**PHASE_1, "actions": [PHASE_1["actions"][0], *PHASE_1["actions"]]},
                "phase-1.json lists action 1.1 twice.",
                id="action-twice",
            ),
            pytest.param(
                {**PHASE_1, "requirements": [{"id": "O-1", "text": O_1, "kind": "outcome"}]},
                "phase-1.json requirement has unknown field kind.",
                id="quote-field",
            ),
            pytest.param(
                {**PHASE_1, "builds_on": [{"symbol": "SessionStatus", "path": "models.py", "line": 3}]},
                "phase-1.json builds_on entry has unknown field line.",
                id="builds-on-field",
            ),
            pytest.param({**PHASE_1, "phase": 0}, "phase-1.json has phase 0; it must be a phase number.", id="phase-0"),
            pytest.param(
                {**PHASE_1, "phase": "1"}, "phase-1.json has phase '1'; it must be a phase number.", id="phase-text"
            ),
            pytest.param(
                {key: value for key, value in PHASE_1.items() if key != "binding"},
                "phase-1.json has no binding list of strings.",
                id="no-binding",
            ),
            pytest.param(
                {**PHASE_1, "owned_elsewhere": [1197]},
                "phase-1.json has no owned_elsewhere list of strings.",
                id="owned-elsewhere-not-text",
            ),
            pytest.param(
                {key: value for key, value in PHASE_1.items() if key != "departures"},
                "phase-1.json has no departures list of strings.",
                id="no-departures",
            ),
            pytest.param(
                {**PHASE_1, "tests": [""]},
                "phase-1.json has no tests list of strings.",
                id="tests-empty-line",
            ),
        ],
    )
    def test_check_breakdown_refuses_a_malformed_phase_file(
        self,
        phase: dict[str, object],
        message: str,
        breakdown: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_phase(breakdown, 1, phase)

        assert _check_breakdown(monkeypatch) == 1

        assert _refusal(capsys) == f"plan.py: .agentic/phases has malformed phase files: {message}\n"
        assert not (breakdown / ".agentic/phases/index.json").exists()

    @pytest.mark.parametrize("name", ["phase-one.json", "phase-01.json"])
    def test_check_breakdown_refuses_a_file_not_named_for_its_phase(
        self, name: str, breakdown: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_json(breakdown / ".agentic/phases" / name, PHASE_1)

        assert _check_breakdown(monkeypatch) == 1

        assert _refusal(capsys) == (
            "plan.py: .agentic/phases has malformed phase files: "
            f"{name} is not named phase-<n>.json for a phase number n, written without leading zeros.\n"
        )
        assert not (breakdown / ".agentic/phases/index.json").exists()

    def test_check_breakdown_refuses_a_phase_file_that_is_not_json(
        self, breakdown: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (breakdown / ".agentic/phases/phase-1.json").write_text("not json", encoding="utf-8")

        assert _check_breakdown(monkeypatch) == 1

        assert _refusal(capsys) == (
            "plan.py: .agentic/phases has malformed phase files: "
            "phase-1.json is not valid JSON: Expecting value: line 1 column 1 (char 0).\n"
        )

    @pytest.mark.parametrize(
        ("change", "problem"),
        [
            pytest.param(
                lambda root: (root / ".agentic/phases/phase-2.json").unlink(),
                "Phase 2 has no phase-2.json. No action covers O-2.",
                id="phase-without-file",
            ),
            pytest.param(
                lambda root: _write_phase(root, 3, PHASE_3),
                "phase-3.json is not a phase of slice S3, whose phases are 1, 2.",
                id="file-without-phase",
            ),
        ],
    )
    def test_check_breakdown_refuses_files_that_do_not_match_the_slices_phases(
        self,
        change: Callable[[Path], None],
        problem: str,
        breakdown: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        change(breakdown)

        assert _check_breakdown(monkeypatch) == 1

        assert _refusal(capsys) == f"plan.py: .agentic/phases does not break down slice S3: {problem}\n"

    @pytest.mark.parametrize(
        ("phase_2", "problem"),
        [
            pytest.param(
                {
                    **PHASE_2,
                    "requirements": [{"id": "O-9", "text": O_2}],
                    "actions": [{**PHASE_2["actions"][0], "requirements": ["O-9"]}],
                },
                "phase-2.json quotes O-9, which requirements.json does not list. No action covers O-2.",
                id="unknown-requirement",
            ),
            pytest.param(
                {**PHASE_2, "requirements": [{"id": "O-2", "text": "`--all` shows discarded sessions."}]},
                "phase-2.json quotes O-2 in words that differ from requirements.json, "
                'which has "`dovo session list --all` shows them with a discarded status.".',
                id="misquoted",
            ),
            pytest.param(
                {**PHASE_2, "decisions": ["D-5", "D-9"]},
                "phase-2.json lists D-9, which plan.json does not have.",
                id="decision-plan-json-lacks",
            ),
            pytest.param(
                {**PHASE_2, "actions": [{**PHASE_2["actions"][0], "requirements": []}]},
                "No action covers O-2.",
                id="uncovered",
            ),
            pytest.param(
                {**PHASE_2, "decisions": []},
                "phase-2.json lacks D-5, whose phases in plan.json include 2.",
                id="decision-missing",
            ),
            pytest.param(
                {**PHASE_2, "decisions": ["D-4", "D-5"]},
                "phase-2.json lists D-4, whose phases in plan.json do not include 2.",
                id="decision-not-for-the-phase",
            ),
            pytest.param(
                {**PHASE_2, "depends_on": [2, 7]},
                "phase-2.json depends on phase 2, 7, which must each be an earlier phase of slice S3.",
                id="depends-on-no-earlier-phase",
            ),
            pytest.param(
                {**PHASE_2, "binding": PHASE_1["binding"]},
                "phase-2.json's binding does not copy, exactly and in order, the 0 code block(s) "
                "under ### Code in plan.md's ## Phase 2, which are [].",
                id="binding-from-another-phase",
            ),
            pytest.param(
                {**PHASE_2, "owned_elsewhere": PHASE_1["owned_elsewhere"][:1]},
                "phase-2.json's owned_elsewhere does not copy the 2 item(s) of plan.md's ## Owned elsewhere, "
                'which are ["#1197 owns deleting SessionStatus.", "#1228 owns the artifact enums."].',
                id="owned-elsewhere-cut",
            ),
            pytest.param(
                {**PHASE_2, "departures": PHASE_1["departures"]},
                "phase-2.json's departures does not copy the 0 item(s) under ### Decisions "
                "in plan.md's ## Phase 2, which are [].",
                id="departures-from-another-phase",
            ),
            pytest.param(
                {**PHASE_2, "tests": PHASE_1["tests"]},
                "phase-2.json's tests does not copy the 0 item(s) under ### Tests in plan.md's ## Phase 2, which are [].",
                id="tests-from-another-phase",
            ),
        ],
    )
    def test_check_breakdown_refuses_a_phase_that_drops_or_misquotes(
        self,
        phase_2: dict[str, object],
        problem: str,
        breakdown: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_phase(breakdown, 2, phase_2)

        assert _check_breakdown(monkeypatch) == 1

        assert _refusal(capsys) == f"plan.py: .agentic/phases does not break down slice S3: {problem}\n"
        assert not (breakdown / ".agentic/phases/index.json").exists()

    @pytest.mark.parametrize(
        "binding",
        [
            pytest.param(["class SessionListResult(BaseResult):\n    hidden: str"], id="edited"),
            pytest.param([], id="dropped"),
            pytest.param([*PHASE_1["binding"], "def extra() -> None: ..."], id="added"),
        ],
    )
    def test_check_breakdown_refuses_a_binding_that_is_not_the_plans_code(
        self,
        binding: list[str],
        breakdown: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_phase(breakdown, 1, {**PHASE_1, "binding": binding})

        assert _check_breakdown(monkeypatch) == 1

        assert _refusal(capsys) == (
            "plan.py: .agentic/phases does not break down slice S3: phase-1.json's binding does not copy, "
            "exactly and in order, the 1 code block(s) under ### Code in plan.md's ## Phase 1, "
            'which are ["class SessionListResult(BaseResult):\\n    hidden: int"].\n'
        )

    @pytest.mark.parametrize(
        ("field", "heading", "items"),
        [
            pytest.param("departures", "### Decisions", ["**D-4** Filter in the formatter."], id="departure-edited"),
            pytest.param("departures", "### Decisions", [], id="departure-dropped"),
            pytest.param(
                "departures", "### Decisions", [*PHASE_1["departures"], "**D-7** Cache the list."], id="departure-added"
            ),
            pytest.param(
                "tests", "### Tests", ["`tests/cli/session/test_list.py`: lists every session."], id="test-edited"
            ),
            pytest.param("tests", "### Tests", [], id="test-dropped"),
            pytest.param(
                "tests", "### Tests", [*PHASE_1["tests"], "`tests/core/test_extra.py`: more."], id="test-added"
            ),
        ],
    )
    def test_check_breakdown_refuses_departures_or_tests_that_are_not_the_plans_items(
        self,
        field: str,
        heading: str,
        items: list[str],
        breakdown: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_phase(breakdown, 1, {**PHASE_1, field: items})

        assert _check_breakdown(monkeypatch) == 1

        expected = json.dumps(PHASE_1[field])
        assert _refusal(capsys) == (
            f"plan.py: .agentic/phases does not break down slice S3: phase-1.json's {field} does not copy "
            f"the 1 item(s) under {heading} in plan.md's ## Phase 1, which are {expected}.\n"
        )

    def test_check_breakdown_accepts_copies_with_other_line_endings_and_whitespace(
        self, breakdown: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        spread = "  " + O_1.replace(" ", "\n  ")
        _write_phase(
            breakdown,
            1,
            {
                **PHASE_1,
                "requirements": [{"id": "O-1", "text": spread}, {"id": "D-1", "text": ISSUE_D_1}],
                "binding": ["\r\nclass SessionListResult(BaseResult):  \r\n    hidden: int\r\n"],
                "departures": ["**D-4**  Filter in the core,\nnot the formatter. "],
            },
        )

        assert _check_breakdown(monkeypatch) == 0

        assert capsys.readouterr() == ("", "")

    @pytest.mark.parametrize(
        ("code", "binding"),
        [
            pytest.param(
                "````markdown\n```python\nhidden: int\n```\n````\n",
                "```python\nhidden: int\n```",
                id="longer-fence-holds-a-shorter-one",
            ),
            pytest.param(
                "```text\n~~~\n## Phase 2: not a heading\n```\n",
                "~~~\n## Phase 2: not a heading",
                id="tilde-line-inside-backticks",
            ),
        ],
    )
    def test_check_breakdown_reads_a_fence_only_closed_by_its_own_kind(
        self,
        code: str,
        binding: str,
        breakdown: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        plan_md = (
            f"## Phase 1: Hide discarded sessions\n\n### Code\n\n{code}\n"
            "## Phase 2: Show them with --all\n\n### Code\n\n```python\nshown: int\n```\n"
        )
        (breakdown / ".agentic/plan.md").write_text(plan_md, encoding="utf-8")
        _write_phase(breakdown, 1, {**PHASE_1, "departures": [], "tests": [], "owned_elsewhere": []})
        _write_phase(breakdown, 2, {**PHASE_2, "owned_elsewhere": []})

        assert _check_breakdown(monkeypatch) == 1

        assert _refusal(capsys) == (
            "plan.py: .agentic/phases does not break down slice S3: phase-1.json's binding does not copy, "
            "exactly and in order, the 1 code block(s) under ### Code in plan.md's ## Phase 1, "
            f"which are {json.dumps([binding])}. phase-2.json's binding does not copy, exactly and in order, "
            """the 1 code block(s) under ### Code in plan.md's ## Phase 2, which are ["shown: int"].\n"""
        )

    def test_check_breakdown_reads_owned_elsewhere_items_whole(
        self, breakdown: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        owned = (
            "## Owned elsewhere\n\n"
            "Pointers to other issues:\n\n"
            "- #1197 owns deleting\nSessionStatus, read lazily.\n"
            "  - including its enum members.\n"
            "1. #1228 owns the artifact enums.\n"
            "2. #1230 owns the docs.\n\n"
            "Not an item.\n\n"
        )
        plan_md = f"# Slice S3 plan\n\n{owned}{PLAN_MD[PLAN_MD.index('## Phase 1') :]}"
        (breakdown / ".agentic/plan.md").write_text(plan_md, encoding="utf-8")

        assert _check_breakdown(monkeypatch) == 1

        expected = [
            "#1197 owns deleting SessionStatus, read lazily. - including its enum members.",
            "#1228 owns the artifact enums.",
            "#1230 owns the docs.",
        ]
        problem = (
            "phase-{n}.json's owned_elsewhere does not copy the 3 item(s) of plan.md's ## Owned elsewhere, "
            f"which are {json.dumps(expected)}."
        )
        assert _refusal(capsys) == (
            f"plan.py: .agentic/phases does not break down slice S3: {problem.format(n=1)} {problem.format(n=2)}\n"
        )

        _write_phase(breakdown, 1, {**PHASE_1, "owned_elsewhere": expected})
        _write_phase(breakdown, 2, {**PHASE_2, "owned_elsewhere": expected})

        assert _check_breakdown(monkeypatch) == 0

    def test_check_breakdown_requires_a_decision_with_no_phases_in_every_phase(
        self, breakdown: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        decisions = [*BREAKDOWN_DECISIONS, {**HUMAN, "id": "D-6", "phases": []}]
        _write_json(
            breakdown / ".agentic/plan.json", {"open_questions": [], "decisions": decisions, "slices": BREAKDOWN_SLICES}
        )

        assert _check_breakdown(monkeypatch) == 1

        assert _refusal(capsys) == (
            "plan.py: .agentic/phases does not break down slice S3: "
            "phase-1.json lacks D-6; a decision with no phases applies to every phase. "
            "phase-2.json lacks D-6; a decision with no phases applies to every phase.\n"
        )

        _write_phase(breakdown, 1, {**PHASE_1, "decisions": ["D-4", "D-6"]})
        _write_phase(breakdown, 2, {**PHASE_2, "decisions": ["D-5", "D-6"]})

        assert _check_breakdown(monkeypatch) == 0

    def test_check_breakdown_refuses_an_unreadable_plan_md(
        self, breakdown: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (breakdown / ".agentic/plan.md").unlink()

        assert _check_breakdown(monkeypatch) == 1

        assert _refusal(capsys).startswith("plan.py: .agentic/plan.md cannot be read: ")

    @pytest.mark.parametrize(
        ("path", "document", "message"),
        [
            pytest.param(
                ".agentic/plan.json",
                {"open_questions": [], "decisions": [], "slices": [{"id": "S3", "phases": []}]},
                ".agentic/plan.json lists no phases for slice S3.",
                id="slice-unphased",
            ),
            pytest.param(
                ".agentic/requirements.json",
                {"requirements": [{"id": "O-1", "kind": "outcome"}]},
                ".agentic/requirements.json: Requirement 1 has no text.",
                id="requirement-malformed",
            ),
        ],
    )
    def test_check_breakdown_refuses_a_malformed_input(
        self,
        path: str,
        document: object,
        message: str,
        breakdown: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_json(breakdown / path, document)

        assert _check_breakdown(monkeypatch) == 1

        assert _refusal(capsys) == f"plan.py: {message}\n"


# --- check-slices ----------------------------------------------------------------

THREE_OUTCOMES = {
    **REQUIREMENTS,
    "requirements": [*REQUIREMENTS["requirements"], {"id": "O-3", "kind": "outcome", "text": "Archived too."}],
}


def _check_slices(workspace: Path, monkeypatch: pytest.MonkeyPatch, *slices: dict[str, object]) -> int:
    """Run plan.py check-slices on a plan.json of slices against THREE_OUTCOMES, both on the default paths."""
    _write_json(workspace / ".agentic/plan.json", {"slices": list(slices)})
    _write_json(workspace / ".agentic/requirements.json", THREE_OUTCOMES)
    return _main(monkeypatch, "check-slices")


class PlanCheckSlicesTests:
    @pytest.mark.parametrize(
        "slices",
        [
            pytest.param(
                [{"id": "S1", "outcomes": ["O-1", "O-2"]}, {"id": "S2", "outcomes": ["O-3"]}], id="one-slice-each"
            ),
            pytest.param(
                [{"id": "S1", "outcomes": ["O-1", "O-3 (reply)"]}, {"id": "S2", "outcomes": ["O-2", "O-3 (fixer)"]}],
                id="split-outcome",
            ),
        ],
    )
    def test_check_slices_passes_when_each_outcome_belongs_to_one_slice_or_to_marked_parts(
        self,
        slices: list[dict[str, object]],
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert _check_slices(workspace, monkeypatch, *slices) == 0

        assert capsys.readouterr() == ("", "")

    @pytest.mark.parametrize(
        ("slices", "problems"),
        [
            pytest.param(
                [{"id": "S1", "outcomes": ["O-1"]}, {"id": "S2", "outcomes": ["O-3"]}],
                "O-2 belongs to no slice.",
                id="orphan-outcome",
            ),
            pytest.param(
                [{"id": "S1", "outcomes": ["O-1", "O-2"]}, {"id": "S2", "outcomes": ["O-2 (reply)", "O-3"]}],
                "O-2 belongs to S1, S2 without a split marker on each, as in O-2 (reply).",
                id="outcome-in-two-slices",
            ),
            pytest.param(
                [{"id": "S1", "outcomes": ["O-1"]}, {"id": "S2", "outcomes": []}],
                "O-2 belongs to no slice. O-3 belongs to no slice.",
                id="every-problem-named",
            ),
            pytest.param(
                [{"id": "S1", "outcomes": ["O-1", "O-2", "O-9"]}, {"id": "S2", "outcomes": ["O-3", "O-9"]}],
                "S1 lists O-9, which the issue does not have. S2 lists O-9, which the issue does not have.",
                id="outcome-the-issue-does-not-have",
            ),
            pytest.param(
                [{"id": "S1", "outcomes": ["O-1", "O-2 (reply)", "O-2 (fixer)"]}, {"id": "S2", "outcomes": ["O-3"]}],
                "O-2 is split into parts, but only S1 lists it; list it whole.",
                id="parts-in-one-slice",
            ),
        ],
    )
    def test_check_slices_refuses_naming_each_outcome_not_delivered_once(
        self,
        slices: list[dict[str, object]],
        problems: str,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert _check_slices(workspace, monkeypatch, *slices) == 1

        assert _refusal(capsys) == (
            f"plan.py: .agentic/plan.json's slices do not deliver each outcome of the issue once: {problems}\n"
        )

    def test_check_slices_refuses_an_outcome_entry_it_cannot_read(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert _check_slices(workspace, monkeypatch, {"id": "S1", "outcomes": ["O-1", "outcome 2"]}) == 1

        assert _refusal(capsys) == (
            "plan.py: .agentic/plan.json: slice S1 lists outcome 'outcome 2', which is not O-<n> or O-<n> (<part>).\n"
        )
