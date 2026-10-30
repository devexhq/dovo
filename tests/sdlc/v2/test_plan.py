"""Tier 1 contract: sdlc v2's plan.py checks a slice plan's decisions, posts them to the issue and reads them back."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
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


_load_script("plan_formats", "plan_formats")
PLAN = _load_script("plan", "sdlc_v2_plan")

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
    their third word (`view`, `comment` or `graphql`).
    """

    def __init__(self, body: str, comments: list[object], failing: frozenset[str] = frozenset()) -> None:
        self.body = body
        self.comments = comments
        self.failing = failing
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str) -> str:
        kind = args[2]
        assert kind in {"view", "comment", "graphql"}, f"ran {args}"
        self.calls.append((*args[:5], Path(args[5]).read_text(encoding="utf-8")) if kind == "comment" else args)
        if kind in self.failing:
            raise subprocess.CalledProcessError(1, list(args), stderr="HTTP 502\n")
        if kind == "view":
            return json.dumps({"body": self.body, "comments": self.comments})
        return ""


def _run(monkeypatch: pytest.MonkeyPatch, fake: FakeGh, *argv: str) -> int:
    """Run plan.py's main with argv against fake."""
    monkeypatch.setattr(PLAN, "run", fake)
    return _main(monkeypatch, *argv)


def _post(monkeypatch: pytest.MonkeyPatch, fake: FakeGh, session: str = SESSION) -> int:
    """Run plan.py decisions-comment for slice S2 of issue 1300 against fake."""
    argv = ["decisions-comment", "--plan", ".agentic/plan.json", "--issue", "1300", "--slice", SLICE]
    return _run(monkeypatch, fake, *argv, "--session", session)


class PlanBodyShaTests:
    def test_body_sha_hashes_a_fixed_description(self) -> None:
        assert PLAN.body_sha(DESCRIPTION) == DESCRIPTION_SHA

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param(DESCRIPTION + "\n## Work plan\n\n| S1 | merged |\n", id="work-plan-added"),
            pytest.param(
                DESCRIPTION.replace("## Scope", "## Feature contract\n\nTarget contract.\n\n## Scope"),
                id="feature-contract-added",
            ),
            pytest.param(DESCRIPTION.replace("\n", "\r\n") + "\r\n\r\n", id="crlf-and-trailing-newlines"),
            pytest.param(DESCRIPTION.replace("#925.", "#925.   "), id="trailing-spaces"),
        ],
    )
    def test_body_sha_ignores_generated_sections_and_whitespace(self, body: str) -> None:
        assert PLAN.body_sha(body) == DESCRIPTION_SHA

    def test_body_sha_changes_with_the_written_description(self) -> None:
        assert PLAN.body_sha(DESCRIPTION.replace("Only", "Just")) != DESCRIPTION_SHA


class PlanDecisionsCommentTests:
    def test_decisions_comment_posts_every_decision_on_one_line_each(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_plan(workspace, [], [HUMAN, ASSUMED, PLANNER, REPLACING])
        fake = FakeGh(DESCRIPTION, [])

        assert _post(monkeypatch, fake) == 0

        assert fake.calls == [VIEW, ("gh", "issue", "comment", "1300", "--body-file", POSTED)]
        assert capsys.readouterr() == ("", "")

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
            DESCRIPTION.replace("\n", "\r\n") + "\n## Work plan\n\n| S1 | merged |\n",
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
        assert written == {"schema_version": 1, "issue": 1300, "decisions": []}

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
            pytest.param(LEGACY_BODY.replace("2. A locked", "1. A locked"), "EC-1", id="error-case"),
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
