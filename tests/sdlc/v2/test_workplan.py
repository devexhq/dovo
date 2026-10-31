"""Tier 1 contract: sdlc v2's workplan.py renders, picks, starts, claims and lands an issue's slices in its work plan comment, the only comment it posts."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType

import pytest

from tests.lint.astlib import REPO_ROOT

EPIC = 925
PLAN_SESSION = "blueprint_plan01"
CODE_SESSION = "blueprint_code02"
BODY_SHA = "3e9b42c06706d5d412cbb04e4ae8f5aebf66cf9f669aff8088c050810507209e"
INTRO = "Intro paragraph a person wrote.\n"
MARKER = "<!-- dovo:workplan schema_version=1 -->"
BY_HAND = "<!-- dovo:workplan schema_version=1 maintained=by-hand -->\n**Work plan** (edited by hand)\n"
V1_BODY = 'Intro.\n\n## Work plan\n<!-- dovo:workplan {"plan_session":"bp_v1","slices":[]} -->\n'
V1_REFUSAL = (
    f"workplan.py: #{EPIC} was planned with v1's description-based work plan: its description holds a "
    "`<!-- dovo:workplan {` line, which v2 never reads; work it with the v1 blueprints\n"
)


def _load_script() -> ModuleType:
    """Load sdlc v2's workplan.py by path, since its folder is not a package."""
    spec = importlib.util.spec_from_file_location(
        "sdlc_v2_workplan", REPO_ROOT / ".dovo/catalog/scripts/sdlc/v2/workplan.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["sdlc_v2_workplan"] = module
    spec.loader.exec_module(module)
    return module


WORKPLAN = _load_script()


class FakeGh:
    """A `run` double answering the gh calls workplan.py makes, against in-memory issues, comments and pull requests.

    It has `run`'s signature, so it replaces it at the subprocess boundary. `workplans` gives each issue a
    work plan comment; every issue's description is INTRO unless `bodies` says otherwise.
    `race[(issue, n)]` is the text another writer stores in that issue's work plan comment right after
    the n-th read of its comments, to exercise compare-and-set. `failing_links` makes that many
    sub-issue POSTs fail, and `create_output` replaces what `gh issue create` prints. `labels` holds each
    issue's labels, which `--label` on create and `--add-label` on edit add to and `--remove-label` takes
    from. `pr_bodies` holds each pull request's description, which `gh pr edit --body-file` replaces.
    """

    def __init__(self, workplans: Mapping[int, str], bodies: Mapping[int, str] | None = None) -> None:
        self.bodies: dict[int, str] = {EPIC: INTRO, **(bodies or {})}
        self.comments: dict[int, list[dict[str, object]]] = {}
        self.next_comment = 6_513_400_000
        for issue, text in workplans.items():
            self._comment(issue, text)
        self.labels: dict[int, list[str]] = {}
        self.closed: set[int] = set()
        self.pr_states: dict[int, str] = {}
        self.pr_bodies: dict[int, str] = {}
        self.pr_edits: list[int] = []
        self.race: dict[tuple[int, int], str] = {}
        self.reads: dict[int, int] = {}
        self.edits: list[int] = []
        self.patched: list[int] = []
        self.created: list[tuple[str, str]] = []
        self.sub_issues: list[tuple[int, str]] = []
        self.removed_labels: list[tuple[int, str]] = []
        self.failing_links = 0
        self.create_output: str | None = None
        self.next_number = 1300

    def __call__(self, *args: str) -> str:
        if args[:3] == ("gh", "issue", "view"):
            return self._view(int(args[3]), args[5])
        if args[:3] == ("gh", "pr", "view"):
            pr = int(args[3])
            return json.dumps({"state": self.pr_states[pr]} if args[5] == "state" else {"body": self.pr_bodies[pr]})
        if args[:4] == ("gh", "pr", "edit", args[3]) and args[4] == "--body-file":
            self.pr_bodies[int(args[3])] = Path(args[5]).read_text(encoding="utf-8")
            self.pr_edits.append(int(args[3]))
            return ""
        if args[:3] == ("gh", "issue", "edit"):
            return self._edit(int(args[3]), args[4:])
        if args[:3] == ("gh", "issue", "comment"):
            self._comment(int(args[3]), Path(args[5]).read_text(encoding="utf-8"))
            return ""
        if args[:3] == ("gh", "issue", "create"):
            return self._create(args[4], Path(args[6]).read_text(encoding="utf-8"), args[7:])
        if args[:3] == ("gh", "repo", "view"):
            return "o/r"
        assert args[:2] == ("gh", "api"), f"ran {args}"
        return self._api(args)

    def workplan(self, issue: int = EPIC) -> str:
        """Return the text of issue's one work plan comment."""
        texts = [str(item["body"]) for item in self.comments.get(issue, []) if str(item["body"]).startswith(MARKER)]
        assert len(texts) == 1, texts
        return texts[0]

    def texts(self, issue: int) -> list[str]:
        """Return the text of every comment on issue, oldest first."""
        return [str(item["body"]) for item in self.comments.get(issue, [])]

    def _comment(self, issue: int, text: str) -> None:
        self.comments.setdefault(issue, []).append({"id": self.next_comment, "body": text})
        self.next_comment += 1

    def _view(self, issue: int, fields: str) -> str:
        if fields == "comments":
            self.reads[issue] = self.reads.get(issue, 0) + 1
            if (issue, self.reads[issue]) in self.race:
                self._race(issue, self.race.pop((issue, self.reads[issue])))
        comments = [
            {"body": item["body"], "url": f"https://github.com/o/r/issues/{issue}#issuecomment-{item['id']}"}
            for item in self.comments.get(issue, [])
        ]
        view: dict[str, object] = {
            "body": self.bodies.get(issue, ""),
            "state": "CLOSED" if issue in self.closed else "OPEN",
            "labels": [{"name": name} for name in self.labels.get(issue, [])],
            "comments": comments,
        }
        return json.dumps({field: view[field] for field in fields.split(",")})

    def _race(self, issue: int, text: str) -> None:
        for item in self.comments[issue]:
            if str(item["body"]).startswith(MARKER):
                item["body"] = text

    def _edit(self, issue: int, flags: tuple[str, ...]) -> str:
        for flag, value in zip(flags[::2], flags[1::2], strict=True):
            if flag == "--remove-label":
                self.removed_labels.append((issue, value))
                self.labels[issue] = [name for name in self.labels.get(issue, []) if name != value]
            elif flag == "--add-label":
                self.labels.setdefault(issue, []).append(value)
            else:
                assert flag == "--body-file"
                self.bodies[issue] = Path(value).read_text(encoding="utf-8")
                self.edits.append(issue)
        return ""

    def _create(self, title: str, body: str, flags: tuple[str, ...]) -> str:
        number = self.next_number
        self.next_number += 1
        self.bodies[number] = body
        self.labels[number] = [value for flag, value in zip(flags[::2], flags[1::2], strict=True) if flag == "--label"]
        self.created.append((title, body))
        return self.create_output or f"Creating issue in o/r\n\nhttps://github.com/o/r/issues/{number}"

    def _patch(self, args: tuple[str, ...]) -> str:
        comment_id = int(args[4].rsplit("/", 1)[-1])
        assert args[4] == f"repos/{{owner}}/{{repo}}/issues/comments/{comment_id}", f"ran {args}"
        assert args[5] == "-F", f"ran {args}"
        text = Path(args[6].removeprefix("body=@")).read_text(encoding="utf-8")
        for item in (item for items in self.comments.values() for item in items):
            if item["id"] == comment_id:
                item["body"] = text
        self.patched.append(comment_id)
        return "{}"

    def _api(self, args: tuple[str, ...]) -> str:
        if args[2:4] == ("--method", "PATCH"):
            return self._patch(args)
        endpoint = f"repos/o/r/issues/{EPIC}/sub_issues"
        if args[-2:] == ("--jq", ".id"):
            return str(90_000 + int(args[2].rsplit("/", 1)[-1]))
        if args[2:4] == ("--paginate", endpoint):
            return "\n".join(str(int(value.rsplit("=", 1)[-1]) - 90_000) for _, value in self.sub_issues)
        assert args[2:5] == ("--method", "POST", endpoint), f"ran {args}"
        if self.failing_links:
            self.failing_links -= 1
            raise subprocess.CalledProcessError(1, list(args), stderr="HTTP 502")
        self.sub_issues.append((EPIC, args[-1]))
        return "{}"


def _slice(slice_id: str, **overrides: object) -> dict[str, object]:
    """Return a plan.json slice with slice_id and overrides, delivering O-1."""
    data: dict[str, object] = {
        "id": slice_id,
        "title": f"Title {slice_id}",
        "phases": [1],
        "depends_on": [],
        "blocked_by": [],
        "outcomes": ["O-1"],
        "owns": [],
        "lint_gates": [],
        "pilot": False,
        "closing_docs": False,
        "estimate": {"src_docs": 100, "tests": 100, "basis": "x"},
        "size": "s",
    }
    data.update(overrides)
    return data


def _row(slice_id: str, **overrides: object) -> object:
    """Return a stored work plan slice row with slice_id and overrides, delivering O-1."""
    defaults: dict[str, object] = {
        "id": slice_id,
        "title": f"Title {slice_id}",
        "depends_on": [],
        "blocked_by": [],
        "closing_docs": False,
        "outcomes": ["O-1"],
        "size": "s",
    }
    defaults.update(overrides)
    return WORKPLAN.Slice(**defaults)


def _workplan(*rows: object, head: str = "", body_sha: str | None = None) -> str:
    """Return a work plan comment holding head, then a work plan of rows from PLAN_SESSION revision 3."""
    plan = WORKPLAN.WorkPlan(PLAN_SESSION, 3, list(rows), body_sha)
    return WORKPLAN.with_workplan(f"{MARKER}\n\n{head}" if head else f"{MARKER}\n", plan)


def _stored(text: str) -> dict[str, object]:
    """Return the dovo:workplan JSON stored in text."""
    match = WORKPLAN.WORKPLAN_COMMENT_RE.search(text)
    assert match is not None
    return json.loads(match.group(1))


def _rows(text: str) -> list[dict[str, object]]:
    """Return the slice rows of the dovo:workplan JSON stored in text."""
    rows = _stored(text)["slices"]
    assert isinstance(rows, list)
    return [row for row in rows if isinstance(row, dict)]


def _main(monkeypatch: pytest.MonkeyPatch, fake: FakeGh, *argv: str) -> int:
    """Run workplan.py's main with argv against fake."""
    monkeypatch.setattr(sys, "argv", ["workplan.py", *argv])
    monkeypatch.setattr(WORKPLAN, "run", fake)
    return WORKPLAN.main()


def _write_json(path: Path, data: object) -> str:
    """Write data as JSON to path and return the path as a string."""
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


CONTRACT: dict[str, object] = {
    "grounded_at": "944de0ce",
    "problem": "Output shapes disagree.",
    "existing_issues": [{"issue": 1131, "role": "child", "slices": ["S1"]}],
    "prerequisites": [{"issue": 1192, "for": ["S2"], "makes_true": "results carry errors"}],
    "non_goals": [
        {"text": "Colour", "kind": "excluded", "owners": [], "reason": "cosmetic"},
        {"text": "Locks", "kind": "owned", "owners": [999], "reason": None},
    ],
    "target": [
        {
            "surface": "JSON envelope",
            "entry": "results.md#envelope",
            "end_state": "one envelope",
            "baseline": {"as_of": "main@944de0ce", "text": "two envelopes"},
            "touched_by": ["S1", "S2"],
        }
    ],
    "conventions": [{"rule": "Use ok", "entry": None, "new_in": "S1"}],
    "exemplars": [{"path": "src/a.py", "why": "Shortest.", "conforms": False, "fixed_by": "S2"}],
    "overrides": [{"issue": 1060, "quote": "keep it", "replacement": "drop it", "per": "D1"}],
    "decisions": [
        {
            "id": "D1",
            "decision": "One envelope",
            "why": "Agreement",
            "settled_by": {"kind": "user", "ref": "2026-10-28", "confirmed": None},
        }
    ],
    "inventory": {
        "sha": "944de0ce",
        "items": [{"kind": "envelopes", "count": 2, "slice": "S1", "command": "rg x", "sites": ["src/a.py:3"]}],
    },
    "ownership": [{"surface": "envelope", "aspect": "create", "owner": "S1", "extended_by": ["S2"]}],
    "lint_gates": [
        {"check": "tests/lint/test_x.py", "extends": False, "enforces": "one envelope", "slice": "S1", "entry": None}
    ],
    "checkpoints": [{"after": ["S1"], "kind": "pilot"}, {"before": "S2", "kind": "baseline"}],
}

# What an earlier version wrote into the work plan comment: a feature contract before the work plan, and
# "as landed" reports after its JSON line. The scripts still run on such a comment and never read either.
OLD_CONTRACT_SECTION = "## Feature contract\nGrounded at `main@944de0ce`.\n\n### Problem\nOutput shapes disagree.\n"
OLD_LANDED = "\n### S1 as landed (#1215)\n#### Departures\n- Renamed a helper.\n"
# An earlier version's ready stamp and planning-decisions comments, which the scripts ignore.
OLD_READY_COMMENT = (
    f"<!-- dovo:ready schema_version=1 body_sha={BODY_SHA} -->\nReady to plan at abc1234 (2026-10-28).\n"
)
OLD_DECISIONS_COMMENT = (
    f"<!-- dovo:decisions schema_version=1 slice=all session=bp_old body_sha={BODY_SHA} -->\n"
    "**Planning decisions** (2026-10-28, slice all, session bp_old)\n\n- **D-3** Keep v1. *Planner, approved with the plan.*\n"
)
PARENT_BODY = (
    "## Why\nOutput shapes disagree, so callers parse two envelopes.\n\n"
    "## Outcomes\n- **O-1** Every command prints one envelope.\n- **O-2** Errors carry a code.\n"
    "- **O-3** The docs show the envelope.\n\n"
    "## Decisions\n- **D-1** One envelope for every command. Accepted.\n\n"
    "## Done when\nO-1 to O-3 hold.\n"
)
PLAN_DECISION = {
    "id": "D-2",
    "was": "OQ-1",
    "decision": "Drop v1 now.",
    "settled_by": "assumed_accepted",
    "rejected": [],
    "phases": [],
    "date": "2026-10-29",
}


def _render(monkeypatch: pytest.MonkeyPatch, fake: FakeGh, plan: str, revision: str = "1", *extra: str) -> int:
    """Run render for EPIC from plan as PLAN_SESSION at revision, with extra arguments."""
    argv = ("render", "--issue", str(EPIC), "--plan", plan, "--session", PLAN_SESSION, "--revision", revision)
    return _main(monkeypatch, fake, *argv, *extra)


class RenderTests:
    def test_creates_the_work_plan_comment_and_leaves_the_description_alone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGh({})
        plan = _write_json(tmp_path / "plan.json", {"contract": None, "slices": [_slice("S1")]})

        assert _render(monkeypatch, fake, plan, "2", "--body-sha", BODY_SHA, "--ready-body-sha", BODY_SHA) == 0

        stored = (
            '{"plan_session":"blueprint_plan01","plan_revision":2,"slices":[{"id":"S1","title":"Title S1",'
            '"depends_on":[],"blocked_by":[],"closing_docs":false,"outcomes":["O-1"],"status":"pending","pr":null,'
            '"session":null,"size":"s","child":null,"phased_session":null,"phased_body_sha":null,'
            f'"ready_body_sha":"{BODY_SHA}"}}],"body_sha":"{BODY_SHA}"}}'
        )
        assert fake.texts(EPIC) == [
            f"{MARKER}\n"
            "\n"
            "## Work plan\n"
            "Plan Session ID: `blueprint_plan01` (plan revision 2)\n"
            "\n"
            "| Slice | Scope | Outcomes | Depends on | Status | Issue | PR | Code session |\n"
            "|---|---|---|---|---|---|---|---|\n"
            "| S1 | Title S1 | O-1 | — | pending | — | — | — |\n"
            "\n"
            f"<!-- dovo:workplan {stored} -->\n"
        ]
        assert (fake.bodies[EPIC], fake.edits, fake.created, fake.sub_issues) == (INTRO, [], [], [])

    def test_epic_posts_only_the_work_plan_and_a_second_render_edits_the_same_comment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slices = [_slice("S1", pilot=True), _slice("S2", depends_on=["S1"], blocked_by=[1192], outcomes=["O-2"])]
        plan = _write_json(tmp_path / "plan.json", {"contract": CONTRACT, "slices": slices, "decisions": []})
        fake = FakeGh({})

        assert _render(monkeypatch, fake, plan, "1") == 0
        first = fake.workplan()
        assert _render(monkeypatch, fake, plan, "2") == 0

        assert first.startswith(f"{MARKER}\n\n## Work plan\n")
        assert "Feature contract" not in first
        assert "| S2 | Title S2 | O-2 | S1, #1192 | pending | — | — | — |" in first
        assert fake.texts(EPIC) == [
            first.replace("(plan revision 1)", "(plan revision 2)").replace('"plan_revision":1', '"plan_revision":2')
        ]
        assert fake.patched == [6_513_400_000]
        assert (fake.bodies[EPIC], fake.edits, fake.created, fake.sub_issues) == (INTRO, [], [], [])

    def test_renders_over_an_earlier_versions_comments_and_leaves_them_alone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = _row("S1", status="merged", pr=1215, session="bp_c")
        old = _workplan(row, _row("S2", outcomes=["O-2"]), head=OLD_CONTRACT_SECTION + "\n") + OLD_LANDED
        fake = FakeGh({EPIC: OLD_READY_COMMENT})
        fake.comments[EPIC].append({"id": 7, "body": old})
        fake.comments[EPIC].append({"id": 8, "body": OLD_DECISIONS_COMMENT})
        slices = [_slice("S1"), _slice("S2", outcomes=["O-2"])]
        plan = _write_json(tmp_path / "plan.json", {"contract": CONTRACT, "slices": slices})

        assert _render(monkeypatch, fake, plan, "4") == 0

        texts = fake.texts(EPIC)
        assert (texts[0], texts[2]) == (OLD_READY_COMMENT, OLD_DECISIONS_COMMENT)
        assert texts[1].startswith(f"{MARKER}\n\n{OLD_CONTRACT_SECTION}\n## Work plan\n")
        assert "as landed" not in texts[1]
        assert [(row["status"], row["pr"]) for row in _rows(texts[1])] == [("merged", 1215), ("pending", None)]
        assert len(texts) == 3

    def test_never_touches_a_comment_maintained_by_hand(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeGh({EPIC: BY_HAND})
        plan = _write_json(tmp_path / "plan.json", {"contract": None, "slices": [_slice("S1")]})

        assert _render(monkeypatch, fake, plan) == 0

        assert fake.texts(EPIC)[0] == BY_HAND
        assert [row["id"] for row in _rows(fake.workplan())] == ["S1"]
        assert fake.patched == []

    def test_refuses_an_issue_with_two_work_plan_comments(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh({EPIC: _workplan(_row("S1"))})
        fake.comments[EPIC].append({"id": 1, "body": _workplan(_row("S1"))})
        plan = _write_json(tmp_path / "plan.json", {"contract": None, "slices": [_slice("S1")]})

        assert _render(monkeypatch, fake, plan) == 1

        assert capsys.readouterr().err == f"workplan.py: #{EPIC} has 2 work plan comments; delete all but one\n"
        assert fake.patched == []

    def test_rerender_keeps_slice_state_and_children(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        row = _row("S1", status="merged", pr=1215, session="bp_c", child=1131, ready_body_sha=BODY_SHA)
        fake = FakeGh({EPIC: _workplan(row)})
        plan = _write_json(tmp_path / "plan.json", {"contract": None, "slices": [_slice("S1")]})

        assert _render(monkeypatch, fake, plan, "4", "--ready-body-sha", "0" * 64) == 0

        text = fake.workplan()
        assert _stored(text)["plan_revision"] == 4
        assert [(row["status"], row["pr"], row["child"], row["ready_body_sha"]) for row in _rows(text)] == [
            ("merged", 1215, 1131, BODY_SHA)
        ]
        assert "| S1 | Title S1 | O-1 | — | merged | #1131 | #1215 | bp_c |" in text

    @pytest.mark.parametrize("contract", [pytest.param(None, id="plain"), pytest.param(CONTRACT, id="with-contract")])
    def test_phased_records_the_one_slice_as_phased_in_this_session(
        self, contract: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGh({})
        plan = _write_json(tmp_path / "plan.json", {"contract": contract, "slices": [_slice("S1", phases=[1, 2])]})

        assert _render(monkeypatch, fake, plan, "5", "--phased-body-sha", BODY_SHA) == 0

        assert [(row["phased_session"], row["phased_body_sha"], row["status"]) for row in _rows(fake.workplan())] == [
            (PLAN_SESSION, BODY_SHA, "pending")
        ]

    @pytest.mark.parametrize(
        ("slices", "reason"),
        [
            pytest.param(
                [_slice("S1"), _slice("S2", outcomes=["O-2"])],
                "--phased-body-sha needs a plan with one slice, not 2",
                id="two-slices",
            ),
            pytest.param(
                [_slice("S1", phases=[])],
                "--phased-body-sha needs slice S1 to list its phases in plan.json",
                id="no-phases",
            ),
        ],
    )
    def test_phased_refuses_a_plan_whose_one_slice_is_not_phased(
        self,
        slices: list[dict[str, object]],
        reason: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake = FakeGh({})
        plan = _write_json(tmp_path / "plan.json", {"contract": CONTRACT, "slices": slices})

        assert _render(monkeypatch, fake, plan, "5", "--phased-body-sha", BODY_SHA) == 1

        assert capsys.readouterr().err == f"workplan.py: {reason}\n"
        assert fake.texts(EPIC) == []

    @pytest.mark.parametrize(
        ("status", "kept"),
        [
            pytest.param("pending", (None, None), id="pending-drops-it"),
            pytest.param("in_progress", ("bp_old", BODY_SHA), id="started"),
        ],
    )
    def test_rerender_without_phased_keeps_the_phased_plan_only_of_a_started_slice(
        self, status: str, kept: tuple[str | None, str | None], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = _row("S1", status=status, phased_session="bp_old", phased_body_sha=BODY_SHA)
        fake = FakeGh({EPIC: _workplan(row)})
        plan = _write_json(tmp_path / "plan.json", {"contract": None, "slices": [_slice("S1")]})

        assert _render(monkeypatch, fake, plan, "4") == 0

        assert [(row["phased_session"], row["phased_body_sha"]) for row in _rows(fake.workplan())] == [kept]

    def test_records_an_existing_sub_issue_the_plan_maps_to_a_slice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGh({})
        slices = [_slice("S1", child=1131), _slice("S2", outcomes=["O-2"])]
        plan = _write_json(tmp_path / "plan.json", {"contract": CONTRACT, "slices": slices})

        assert _render(monkeypatch, fake, plan) == 0

        text = fake.workplan()
        assert [(row["child"], row["status"]) for row in _rows(text)] == [(1131, "pending"), (None, "pending")]
        assert "| S1 | Title S1 | O-1 | — | pending | #1131 | — | — |" in text
        assert (fake.created, fake.sub_issues) == ([], [])

    def test_refuses_an_issue_planned_with_v1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh({}, {EPIC: V1_BODY})
        plan = _write_json(tmp_path / "plan.json", {"contract": None, "slices": [_slice("S1")]})

        assert _render(monkeypatch, fake, plan) == 1

        assert capsys.readouterr().err == V1_REFUSAL
        assert (fake.texts(EPIC), fake.bodies[EPIC]) == ([], V1_BODY)

    def test_a_comment_changed_twice_refuses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh({EPIC: _workplan(_row("S1"))})
        fake.race[(EPIC, 2)] = _workplan(_row("S1"), head="Once.\n")
        fake.race[(EPIC, 4)] = _workplan(_row("S1"), head="Twice.\n")
        plan = _write_json(tmp_path / "plan.json", {"contract": None, "slices": [_slice("S1")]})

        assert _render(monkeypatch, fake, plan, "4") == 1

        assert capsys.readouterr().err == (
            f"workplan.py: #{EPIC}'s work plan comment kept changing while rendering it; run render again\n"
        )
        assert (fake.patched, fake.workplan()) == ([], _workplan(_row("S1"), head="Twice.\n"))

    @pytest.mark.parametrize(
        ("stored", "planned", "resolved"),
        [
            pytest.param(None, "m", "m", id="plan-sizes-an-unsized-row"),
            pytest.param("s", "m", "m", id="plan-size-wins"),
            pytest.param("l", None, "l", id="sizeless-plan-keeps-stored-size"),
        ],
    )
    def test_rerender_resolves_the_size_from_the_plan_else_the_stored_row(
        self,
        stored: str | None,
        planned: str | None,
        resolved: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake = FakeGh({EPIC: _workplan(_row("S1", size=stored))})
        plan = _write_json(tmp_path / "plan.json", {"contract": CONTRACT, "slices": [_slice("S1", size=planned)]})

        assert _render(monkeypatch, fake, plan, "4") == 0

        assert [row["size"] for row in _rows(fake.workplan())] == [resolved]

    @pytest.mark.parametrize(
        ("plan", "reason"),
        [
            pytest.param({"contract": None, "slices": []}, "plan.json has no slices", id="no-slices"),
            pytest.param(
                {"contract": None, "slices": [_slice("S1"), _slice("S2")]},
                "a plan without a contract must have one slice, not 2",
                id="plain-with-two-slices",
            ),
            pytest.param(
                {"contract": CONTRACT, "slices": [_slice("S1", depends_on=["S2"]), _slice("S2")]},
                "slice S1 depends on S2, which is not an earlier slice",
                id="forward-dependency",
            ),
            pytest.param(
                {"contract": CONTRACT, "slices": [_slice("S1", size="xl")]},
                "slice S1 size must be s, m or l, not 'xl'",
                id="unknown-size",
            ),
        ],
    )
    def test_refuses_malformed_plan(
        self,
        plan: dict[str, object],
        reason: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake = FakeGh({})

        assert _render(monkeypatch, fake, _write_json(tmp_path / "plan.json", plan)) == 1

        assert capsys.readouterr().err == f"workplan.py: {reason}\n"
        assert fake.texts(EPIC) == []


class WriteGuardTests:
    def _claim(self, monkeypatch: pytest.MonkeyPatch, fake: FakeGh) -> int:
        return _main(monkeypatch, fake, "claim", "--issue", str(EPIC), "--slice", "S1", "--session", CODE_SESSION)

    def test_a_comment_changed_once_since_it_was_read_gets_the_change_applied_again(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGh({EPIC: _workplan(_row("S1"), _row("S2"))})
        fake.race[(EPIC, 2)] = _workplan(_row("S1"), _row("S2", title="Renamed by a person"))

        assert self._claim(monkeypatch, fake) == 0

        rows = _rows(fake.workplan())
        assert [(row["title"], row["status"]) for row in rows] == [
            ("Title S1", "in_progress"),
            ("Renamed by a person", "pending"),
        ]
        assert fake.patched == [6_513_400_000]

    def test_a_comment_changed_twice_refuses_naming_the_slice(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh({EPIC: _workplan(_row("S1"))})
        fake.race[(EPIC, 2)] = _workplan(_row("S1", title="Once"))
        fake.race[(EPIC, 4)] = _workplan(_row("S1", title="Twice"))

        assert self._claim(monkeypatch, fake) == 1

        assert capsys.readouterr().err == (
            f"workplan.py: #{EPIC}'s work plan comment kept changing while claiming S1; run claim again\n"
        )
        assert fake.patched == []
        assert _rows(fake.workplan())[0]["title"] == "Twice"


class NextTests:
    @pytest.mark.parametrize(
        ("rows", "outputs"),
        [
            pytest.param(
                [_row("S1")],
                f"slice=S1\nplan_session={PLAN_SESSION}\nplan_revision=3\nbody_sha={BODY_SHA}\nphased_session=\n"
                "landed=\n",
                id="only-slice",
            ),
            pytest.param(
                [_row("S1", status="merged", pr=1215, session="bp_c1"), _row("S2", depends_on=["S1"]), _row("S3")],
                f"slice=S2\nplan_session={PLAN_SESSION}\nplan_revision=3\nbody_sha={BODY_SHA}\nphased_session=\n"
                "landed=S1=bp_c1\n",
                id="middle-slice",
            ),
            pytest.param(
                [
                    _row("S1", status="merged", pr=1215, session="bp_c1"),
                    _row("S2", status="merged", pr=1216, session="bp_c2"),
                    _row("S3"),
                ],
                f"slice=S3\nplan_session={PLAN_SESSION}\nplan_revision=3\nbody_sha={BODY_SHA}\nphased_session=\n"
                "landed=S1=bp_c1 S2=bp_c2\n",
                id="last-slice",
            ),
            pytest.param(
                [_row("S1", phased_session=PLAN_SESSION)],
                f"slice=S1\nplan_session={PLAN_SESSION}\nplan_revision=3\nbody_sha={BODY_SHA}\n"
                f"phased_session={PLAN_SESSION}\nlanded=\n",
                id="phased",
            ),
        ],
    )
    def test_writes_outputs_for_the_picked_slice_and_creates_no_issue(
        self, rows: list[object], outputs: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output = tmp_path / "output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output))
        fake = FakeGh({EPIC: _workplan(*rows, body_sha=BODY_SHA)})

        assert _main(monkeypatch, fake, "next", "--issue", str(EPIC)) == 0

        assert output.read_text(encoding="utf-8") == outputs
        assert (fake.patched, fake.created, fake.sub_issues) == ([], [], [])

    def test_marks_a_merged_pr_before_picking(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        output = tmp_path / "output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output))
        rows = [
            _row("S1", status="in_review", pr=1215, session="bp_c"),
            _row("S2", depends_on=["S1"], blocked_by=[1192]),
        ]
        fake = FakeGh({EPIC: _workplan(*rows)})
        fake.pr_states[1215] = "MERGED"
        fake.closed.add(1192)

        assert _main(monkeypatch, fake, "next", "--issue", str(EPIC)) == 0

        assert [(row["id"], row["status"]) for row in _rows(fake.workplan())] == [("S1", "merged"), ("S2", "pending")]
        assert output.read_text(encoding="utf-8").splitlines()[0] == "slice=S2"
        assert (fake.created, fake.sub_issues, fake.edits) == ([], [], [])

    def test_never_picks_a_handed_off_slice(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        output = tmp_path / "output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output))
        fake = FakeGh({EPIC: _workplan(_row("S1", status="handed_off", child=1300), _row("S2"))})

        assert _main(monkeypatch, fake, "next", "--issue", str(EPIC)) == 0

        assert output.read_text(encoding="utf-8").splitlines()[0] == "slice=S2"

    def test_refuses_an_issue_planned_with_v1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        output = tmp_path / "output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output))
        fake = FakeGh({EPIC: _workplan(_row("S1"))}, {EPIC: V1_BODY})

        assert _main(monkeypatch, fake, "next", "--issue", str(EPIC)) == 1

        assert capsys.readouterr().err == V1_REFUSAL
        assert (fake.patched, output.exists()) == ([], False)

    @pytest.mark.parametrize(
        ("rows", "reason"),
        [
            pytest.param(
                [_row("S1", status="in_review", pr=1215), _row("S2")],
                "S1 is in_review (#1215); finish it before starting another slice",
                id="active-slice",
            ),
            pytest.param(
                [_row("S1", blocked_by=[1192]), _row("S2", depends_on=["S1"])],
                "no slice is ready: S1 is blocked by open #1192; S2 waits on S1",
                id="none-ready",
            ),
            pytest.param(
                [_row("S1", status="handed_off", child=1300), _row("S2", depends_on=["S1"])],
                "no slice is ready: S2 waits on S1",
                id="waits-on-an-open-handed-off-slice",
            ),
            pytest.param(
                [_row("S1", status="merged", pr=1215), _row("S2", status="handed_off", child=1300)],
                "every slice is merged or handed off",
                id="all-merged-or-handed-off",
            ),
        ],
    )
    def test_refuses_with_reason(
        self,
        rows: list[object],
        reason: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        output = tmp_path / "output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output))
        fake = FakeGh({EPIC: _workplan(*rows)})
        fake.pr_states[1215] = "OPEN"

        assert _main(monkeypatch, fake, "next", "--issue", str(EPIC)) == 1

        assert capsys.readouterr().err == f"workplan.py: {reason}\n"
        assert not output.exists()

    @pytest.mark.parametrize(
        "comments", [pytest.param({}, id="no-comment"), pytest.param({EPIC: BY_HAND}, id="by-hand")]
    )
    def test_refuses_an_issue_without_a_work_plan_comment(
        self, comments: dict[int, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh(comments)

        assert _main(monkeypatch, fake, "next", "--issue", str(EPIC)) == 1

        assert capsys.readouterr().err == "workplan.py: the issue has no work plan; run `workplan.py render` first\n"


class NextHoldTests:
    def _argv(self, hold: Path) -> tuple[str, ...]:
        return ("next", "--issue", str(EPIC), "--hold", str(hold))

    @pytest.mark.parametrize(
        ("rows", "holds", "reason"),
        [
            pytest.param(
                [_row("S1", blocked_by=[1192]), _row("S2", depends_on=["S1"])],
                [
                    {"id": "S1", "label": "S1 is blocked by open #1192"},
                    {"id": "S2", "label": "S2 waits on S1"},
                ],
                "no slice is ready: S1 is blocked by open #1192; S2 waits on S1",
                id="none-ready",
            ),
            pytest.param(
                [_row("S1", status="in_review", pr=1215), _row("S2")],
                [{"id": "S1", "label": "S1 is in_review (#1215); finish it before starting another slice"}],
                "S1 is in_review (#1215); finish it before starting another slice",
                id="in-review",
            ),
        ],
    )
    def test_writes_the_holds_and_no_outputs_when_the_slices_wait_on_a_person(
        self,
        rows: list[object],
        holds: list[dict[str, str]],
        reason: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        output = tmp_path / "output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output))
        hold = tmp_path / ".agentic" / "next-hold.json"
        fake = FakeGh({EPIC: _workplan(*rows)})
        fake.pr_states[1215] = "OPEN"

        assert _main(monkeypatch, fake, *self._argv(hold)) == 0

        assert json.loads(hold.read_text(encoding="utf-8")) == {"holds": holds}
        assert capsys.readouterr().err == f"workplan.py: holding: {reason}\n"
        assert not output.exists()

    def test_writes_no_holds_when_a_slice_is_picked(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        output = tmp_path / "output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output))
        hold = tmp_path / "next-hold.json"
        fake = FakeGh({EPIC: _workplan(_row("S1", blocked_by=[1192]))})
        fake.closed.add(1192)

        assert _main(monkeypatch, fake, *self._argv(hold)) == 0

        assert json.loads(hold.read_text(encoding="utf-8")) == {"holds": []}
        assert output.read_text(encoding="utf-8").splitlines()[0] == "slice=S1"

    @pytest.mark.parametrize(
        ("rows", "reason"),
        [
            pytest.param(
                [_row("S1", status="in_progress", session=CODE_SESSION), _row("S2")],
                "S1 is in_progress; finish it before starting another slice",
                id="in-progress",
            ),
            pytest.param(
                [_row("S1", status="merged", pr=1215)], "every slice is merged or handed off", id="all-merged"
            ),
        ],
    )
    def test_still_refuses_what_no_person_can_settle_by_merging(
        self,
        rows: list[object],
        reason: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        hold = tmp_path / "next-hold.json"
        fake = FakeGh({EPIC: _workplan(*rows)})

        assert _main(monkeypatch, fake, *self._argv(hold)) == 1

        assert capsys.readouterr().err == f"workplan.py: {reason}\n"
        assert not hold.exists()


class ClaimTests:
    def test_marks_the_slice_in_progress_with_the_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeGh({EPIC: _workplan(_row("S1"))})

        code = _main(monkeypatch, fake, "claim", "--issue", str(EPIC), "--slice", "S1", "--session", CODE_SESSION)

        assert code == 0
        row = _rows(fake.workplan())[0]
        assert (row["status"], row["session"]) == ("in_progress", CODE_SESSION)
        assert f"| S1 | Title S1 | O-1 | — | in_progress | — | — | {CODE_SESSION} |" in fake.workplan()
        assert fake.edits == []

    def test_keeps_the_slices_phased_plan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeGh({EPIC: _workplan(_row("S1", phased_session=PLAN_SESSION, phased_body_sha=BODY_SHA))})

        code = _main(monkeypatch, fake, "claim", "--issue", str(EPIC), "--slice", "S1", "--session", CODE_SESSION)

        assert code == 0
        row = _rows(fake.workplan())[0]
        assert (row["status"], row["phased_session"], row["phased_body_sha"]) == ("in_progress", PLAN_SESSION, BODY_SHA)

    @pytest.mark.parametrize(
        ("row", "reason"),
        [
            pytest.param(
                _row("S1", status="in_progress", session="bp_other"), "S1 is in_progress, not pending", id="taken"
            ),
            pytest.param(_row("S2"), "the work plan has no slice S1", id="unknown"),
        ],
    )
    def test_refuses_a_slice_that_is_not_pending(
        self, row: object, reason: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh({EPIC: _workplan(row)})

        code = _main(monkeypatch, fake, "claim", "--issue", str(EPIC), "--slice", "S1", "--session", CODE_SESSION)

        assert code == 1
        assert capsys.readouterr().err == f"workplan.py: {reason}\n"
        assert fake.patched == []


class LandTests:
    def _argv(self, tmp_path: Path, plan: Mapping[str, object], *extra: str) -> tuple[str, ...]:
        """Return land's argv for S1 with PR 1215, plan and an epic plan holding CONTRACT under tmp_path."""
        plan_path = _write_json(tmp_path / "plan.json", plan)
        base_path = _write_json(tmp_path / "epic-plan.json", {"contract": CONTRACT, "slices": []})
        return (
            "land", "--issue", str(EPIC), "--slice", "S1", "--session", CODE_SESSION,
            "--pr", "1215", "--plan", plan_path, "--base-plan", base_path, *extra,
        )  # fmt: skip

    def _claimed_workplan(self, head: str = "") -> str:
        """Return a work plan comment holding head and a work plan whose S1 this code session claimed."""
        rows = [_row("S1", status="in_progress", session=CODE_SESSION), _row("S2", outcomes=["O-2"])]
        return _workplan(*rows, head=head)

    def _slices(self, **s2: object) -> list[dict[str, object]]:
        return [_slice("S1"), _slice("S2", outcomes=["O-2"], **s2)]

    def test_marks_in_review_and_writes_no_report_or_other_comment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output = tmp_path / "output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output))
        fake = FakeGh({EPIC: self._claimed_workplan()})
        before = fake.workplan()
        plan = {"contract": CONTRACT, "slices": self._slices()}

        assert _main(monkeypatch, fake, *self._argv(tmp_path, plan)) == 0

        text = fake.workplan()
        stored = _stored(text)
        assert (stored["plan_session"], stored["plan_revision"]) == (PLAN_SESSION, 3)
        assert (_rows(text)[0]["status"], _rows(text)[0]["pr"]) == ("in_review", 1215)
        assert "| S1 | Title S1 | O-1 | — | in_review | — | #1215 | blueprint_code02 |" in text
        assert text.endswith("-->\n")
        assert len(text.splitlines()) == len(before.splitlines())
        assert output.read_text(encoding="utf-8") == f"remaining=1\nclosing_lines=Part of #{EPIC} (slice S1 of 2)\n"
        assert (fake.texts(EPIC), fake.bodies[EPIC], fake.edits) == ([text], INTRO, [])

    def test_keeps_an_earlier_versions_contract_section_and_drops_its_landed_reports(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGh({EPIC: self._claimed_workplan(head=OLD_CONTRACT_SECTION + "\n") + OLD_LANDED})
        plan = {"contract": CONTRACT, "slices": self._slices()}

        assert _main(monkeypatch, fake, *self._argv(tmp_path, plan)) == 0

        text = fake.workplan()
        assert text.startswith(f"{MARKER}\n\n{OLD_CONTRACT_SECTION}\n## Work plan\n")
        assert text.endswith("-->\n")
        assert _rows(text)[0]["status"] == "in_review"

    @pytest.mark.parametrize(
        ("s1", "s2", "added", "outputs"),
        [
            pytest.param(
                {}, {"status": "merged", "pr": 1214}, [], f"remaining=0\nclosing_lines=Closes #{EPIC}\n", id="last"
            ),
            pytest.param(
                {"child": 1131},
                {"status": "merged", "pr": 1214},
                [],
                f"remaining=0\nclosing_lines<<WORKPLAN_EOF\nCloses #1131\nCloses #{EPIC}\nWORKPLAN_EOF\n",
                id="own-issue",
            ),
            pytest.param(
                {},
                {"status": "merged", "pr": 1214},
                [_slice("S3", depends_on=["S1"])],
                f"remaining=1\nclosing_lines=Part of #{EPIC} (slice S1 of 3)\n",
                id="amended-plan-adds-a-slice",
            ),
            pytest.param(
                {},
                {"status": "handed_off", "child": 1301},
                [],
                f"remaining=0\nclosing_lines=Closes #{EPIC}\n",
                id="handed-off-issue-closed",
            ),
            pytest.param(
                {},
                {"status": "handed_off", "child": 1302},
                [],
                f"remaining=1\nclosing_lines=Part of #{EPIC} (slice S1 of 2)\n",
                id="handed-off-issue-open",
            ),
        ],
    )
    def test_writes_the_closing_lines_from_the_plan_as_landed(
        self,
        s1: dict[str, object],
        s2: dict[str, object],
        added: list[dict[str, object]],
        outputs: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        output = tmp_path / "output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output))
        rows = [_row("S1", status="in_progress", session=CODE_SESSION, **s1), _row("S2", **s2)]
        fake = FakeGh({EPIC: _workplan(*rows)})
        fake.closed.add(1301)
        plan = {"contract": CONTRACT, "slices": [_slice("S1"), _slice("S2"), *added]}

        assert _main(monkeypatch, fake, *self._argv(tmp_path, plan)) == 0

        assert output.read_text(encoding="utf-8") == outputs

    def test_a_comment_changed_twice_refuses_naming_the_slice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        output = tmp_path / "output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output))
        row = _row("S1", status="in_progress", session=CODE_SESSION)
        fake = FakeGh({EPIC: _workplan(row)})
        fake.race[(EPIC, 2)] = _workplan(row, head="Once.\n")
        fake.race[(EPIC, 4)] = _workplan(row, head="Twice.\n")

        assert _main(monkeypatch, fake, *self._argv(tmp_path, {"contract": None, "slices": [_slice("S1")]})) == 1

        assert capsys.readouterr().err == (
            f"workplan.py: #{EPIC}'s work plan comment kept changing while landing S1; run land again\n"
        )
        assert (fake.patched, fake.workplan()) == ([], _workplan(row, head="Twice.\n"))
        assert not output.exists()

    def test_refuses_a_comment_longer_than_github_allows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh({EPIC: self._claimed_workplan(head="x" * 65_536 + "\n")})
        before = fake.workplan()
        plan = {"contract": CONTRACT, "slices": self._slices()}

        assert _main(monkeypatch, fake, *self._argv(tmp_path, plan)) == 1

        error = capsys.readouterr().err
        match = re.fullmatch(
            r"workplan\.py: the work plan comment would be (?P<size>[\d,]+) characters, over GitHub's 65,536; "
            r"shorten what a person added to it and run the command again\n",
            error,
        )
        assert match is not None, error
        assert int(match["size"].replace(",", "")) > 65_536
        assert (fake.workplan(), fake.patched) == (before, [])

    def test_amended_contract_repoints_the_plan_session_and_stays_out_of_the_comment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGh({EPIC: self._claimed_workplan()})
        amended = {**CONTRACT, "conventions": [{"rule": "Use ok() everywhere", "entry": None, "new_in": "S1"}]}

        assert _main(monkeypatch, fake, *self._argv(tmp_path, {"contract": amended, "slices": self._slices()})) == 0

        text = fake.workplan()
        assert "Use ok() everywhere" not in text
        assert text.startswith(f"{MARKER}\n\n## Work plan\n")
        stored = _stored(text)
        assert (stored["plan_session"], stored["plan_revision"]) == (CODE_SESSION, None)

    def test_a_phased_slice_keeps_its_phased_plan_and_the_plan_session(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = _row("S1", status="in_progress", session=CODE_SESSION, phased_session=PLAN_SESSION)
        fake = FakeGh({EPIC: _workplan(row)})
        plan = {"contract": None, "slices": [_slice("S1", phases=[1, 2])]}

        assert _main(monkeypatch, fake, *self._argv(tmp_path, plan)) == 0

        text = fake.workplan()
        assert _stored(text)["plan_session"] == PLAN_SESSION
        assert [(row["status"], row["phased_session"]) for row in _rows(text)] == [("in_review", PLAN_SESSION)]

    def test_amended_plan_repoints_the_plan_session(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeGh({EPIC: self._claimed_workplan()})
        plan = {"contract": CONTRACT, "slices": self._slices(title="Narrower S2")}

        assert _main(monkeypatch, fake, *self._argv(tmp_path, plan)) == 0

        text = fake.workplan()
        stored = _stored(text)
        assert (stored["plan_session"], stored["plan_revision"]) == (CODE_SESSION, None)
        assert _rows(text)[1]["title"] == "Narrower S2"
        assert f"Plan Session ID: `{CODE_SESSION}`\n" in text

    @pytest.mark.parametrize(
        "redefined",
        [pytest.param({"title": "Rewritten S1"}, id="title"), pytest.param({"outcomes": ["O-2"]}, id="outcomes")],
    )
    def test_refuses_a_plan_that_redefines_a_started_slice(
        self,
        redefined: dict[str, object],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake = FakeGh({EPIC: _workplan(_row("S1", status="in_progress", session=CODE_SESSION))})
        plan = {"contract": None, "slices": [_slice("S1", **redefined)]}

        assert _main(monkeypatch, fake, *self._argv(tmp_path, plan)) == 1

        assert capsys.readouterr().err == "workplan.py: the plan changes S1, which already started\n"
        assert fake.patched == []

    def test_applies_sweep_entries_and_skips_a_changed_body(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh(
            {EPIC: _workplan(_row("S1", status="in_progress", session=CODE_SESSION))},
            {1060: "Old 1060.\n", 1061: "Edited since the sweep was drafted.\n"},
        )
        fake.labels[1060] = ["dovo:ready", "bug"]
        sweep_dir = tmp_path / "sweep"
        sweep_dir.mkdir()
        entries = []
        for issue in (1060, 1061):
            (sweep_dir / f"{issue}.md").write_text(f"New {issue}.\n", encoding="utf-8")
            (sweep_dir / f"{issue}.comment.md").write_text(f"Swept {issue}.\n", encoding="utf-8")
            entries.append(
                {
                    "issue": issue,
                    "expected_body_sha256": hashlib.sha256(f"Old {issue}.\n".encode()).hexdigest(),
                    "body_file": f"sweep/{issue}.md",
                    "comment_file": f"sweep/{issue}.comment.md",
                }
            )
        sweep = _write_json(tmp_path / "sweep.json", entries)
        plan = {"contract": None, "slices": [_slice("S1")]}

        assert _main(monkeypatch, fake, *self._argv(tmp_path, plan, "--sweep", sweep)) == 0

        assert capsys.readouterr().err == (
            "workplan.py: sweep skipped #1061: its body changed since the sweep was drafted\n"
        )
        assert (fake.bodies[1060], fake.bodies[1061]) == ("New 1060.\n", "Edited since the sweep was drafted.\n")
        assert (fake.texts(1060), fake.texts(1061)) == (["Swept 1060.\n"], [])
        assert fake.removed_labels == [(1060, "dovo:ready")]

    def test_sweep_skips_the_issue_and_the_slices_own_issue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        row = _row("S1", status="in_progress", session=CODE_SESSION, child=1131)
        fake = FakeGh({EPIC: _workplan(row)}, {1131: "Old 1131.\n", 1060: "Old 1060.\n"})
        sweep_dir = tmp_path / "sweep"
        sweep_dir.mkdir()
        entries = []
        for issue, old in ((EPIC, INTRO), (1131, "Old 1131.\n"), (1060, "Old 1060.\n")):
            (sweep_dir / f"{issue}.md").write_text(f"New {issue}.\n", encoding="utf-8")
            (sweep_dir / f"{issue}.comment.md").write_text(f"Swept {issue}.\n", encoding="utf-8")
            entries.append(
                {
                    "issue": issue,
                    "expected_body_sha256": hashlib.sha256(old.encode()).hexdigest(),
                    "body_file": f"sweep/{issue}.md",
                    "comment_file": f"sweep/{issue}.comment.md",
                }
            )
        sweep = _write_json(tmp_path / "sweep.json", entries)
        plan = {"contract": None, "slices": [_slice("S1", child=1131)]}

        assert _main(monkeypatch, fake, *self._argv(tmp_path, plan, "--sweep", sweep)) == 0

        skipped = "the slice is worked on it, and only a person writes its description"
        assert capsys.readouterr().err == (
            f"workplan.py: sweep skipped #{EPIC}: {skipped}\nworkplan.py: sweep skipped #1131: {skipped}\n"
        )
        assert (fake.bodies[EPIC], fake.bodies[1131], fake.bodies[1060]) == (INTRO, "Old 1131.\n", "New 1060.\n")
        assert (fake.texts(1131), fake.texts(1060), fake.edits) == ([], ["Swept 1060.\n"], [1060])

    def test_accepts_an_empty_sweep(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeGh({EPIC: _workplan(_row("S1", status="in_progress", session=CODE_SESSION))})
        sweep = _write_json(tmp_path / "sweep.json", [])
        plan = {"contract": None, "slices": [_slice("S1")]}

        assert _main(monkeypatch, fake, *self._argv(tmp_path, plan, "--sweep", sweep)) == 0

        assert (fake.patched, fake.edits) == ([6_513_400_000], [])

    def test_refuses_a_slice_this_session_did_not_claim(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh({EPIC: _workplan(_row("S1"))})
        plan = {"contract": None, "slices": [_slice("S1")]}

        assert _main(monkeypatch, fake, *self._argv(tmp_path, plan)) == 1

        assert capsys.readouterr().err == (
            f"workplan.py: S1 is pending for session None, not in_progress for {CODE_SESSION}\n"
        )
        assert fake.patched == []


class StartTests:
    def _start(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: FakeGh, slice_id: str = "S2") -> int:
        """Run start for slice_id of EPIC with a plan.json holding PLAN_DECISION."""
        plan = _write_json(tmp_path / "plan.json", {"contract": CONTRACT, "slices": [], "decisions": [PLAN_DECISION]})
        return _main(monkeypatch, fake, "start", "--issue", str(EPIC), "--slice", slice_id, "--plan", plan)

    def test_a_sliced_plan_gives_the_slice_a_linked_sub_issue_its_pull_request_closes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output = tmp_path / "output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output))
        rows = [_row("S1", status="merged", pr=1215, session="bp_c1"), _row("S2", outcomes=["O-2", "O-3 (docs)"])]
        fake = FakeGh({EPIC: _workplan(*rows)}, {EPIC: PARENT_BODY})

        assert self._start(tmp_path, monkeypatch, fake) == 0

        assert fake.created == [
            (
                "Title S2",
                f"Slice S2 of #{EPIC} (2 slices): Title S2. The work plan comment on #{EPIC} tracks it.\n"
                "\n"
                "## Why\n"
                "Output shapes disagree, so callers parse two envelopes.\n"
                "\n"
                "## Outcomes\n"
                "- **O-2** Errors carry a code.\n"
                "- **O-3** The docs show the envelope. This slice delivers only its docs part.\n"
                "\n"
                "## Decisions\n"
                "- **D-1** One envelope for every command. Accepted.\n"
                "- **D-2** Drop v1 now.\n"
                "\n"
                "## Done when\n"
                "O-2, O-3 hold.\n",
            )
        ]
        assert fake.labels[1300] == []
        assert fake.sub_issues == [(EPIC, "sub_issue_id=91300")]
        assert [row["child"] for row in _rows(fake.workplan())] == [None, 1300]
        assert "| S2 | Title S2 | O-2, O-3 (docs) | — | pending | #1300 | — | — |" in fake.workplan()
        assert output.read_text(encoding="utf-8") == (
            f"issue=1300\nremaining=0\nclosing_lines<<WORKPLAN_EOF\nCloses #1300\nCloses #{EPIC}\nWORKPLAN_EOF\n"
        )
        assert (len(fake.texts(EPIC)), fake.texts(1300), fake.bodies[EPIC], fake.edits) == (1, [], PARENT_BODY, [])

    def test_a_middle_slice_is_part_of_the_issue_and_a_rerun_reuses_its_sub_issue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output = tmp_path / "output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output))
        fake = FakeGh({EPIC: _workplan(_row("S1"), _row("S2"), _row("S3"))}, {EPIC: PARENT_BODY})

        assert self._start(tmp_path, monkeypatch, fake, "S1") == 0
        assert self._start(tmp_path, monkeypatch, fake, "S1") == 0

        assert len(fake.created) == 1
        assert fake.sub_issues == [(EPIC, "sub_issue_id=91300")]
        closing = (
            f"issue=1300\nremaining=2\nclosing_lines<<WORKPLAN_EOF\nCloses #1300\nPart of #{EPIC} (slice S1 of 3)\n"
        )
        assert output.read_text(encoding="utf-8") == f"{closing}WORKPLAN_EOF\n" * 2

    def test_a_one_slice_plan_gets_no_sub_issue_and_its_pull_request_closes_the_issue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output = tmp_path / "output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output))
        fake = FakeGh({EPIC: _workplan(_row("S1"))}, {EPIC: PARENT_BODY})
        before = fake.workplan()

        assert self._start(tmp_path, monkeypatch, fake, "S1") == 0

        assert output.read_text(encoding="utf-8") == f"issue={EPIC}\nremaining=0\nclosing_lines=Closes #{EPIC}\n"
        assert (fake.created, fake.sub_issues, fake.patched) == ([], [], [])
        assert fake.texts(EPIC) == [before]

    def test_a_slice_with_its_own_issue_keeps_it_linked(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        output = tmp_path / "output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output))
        fake = FakeGh({EPIC: _workplan(_row("S1", child=1131), _row("S2"))})

        assert self._start(tmp_path, monkeypatch, fake, "S1") == 0

        assert (fake.created, fake.sub_issues) == ([], [(EPIC, "sub_issue_id=91131")])
        assert output.read_text(encoding="utf-8").splitlines()[0] == "issue=1131"

    def test_a_link_failure_keeps_the_record_and_a_rerun_links_without_creating(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh({EPIC: _workplan(_row("S1"), _row("S2"))}, {EPIC: PARENT_BODY})
        fake.failing_links = 1

        assert self._start(tmp_path, monkeypatch, fake) == 1
        assert capsys.readouterr().err == (
            f"workplan.py: linking #1300 failed (gh api --method POST repos/o/r/issues/{EPIC}/sub_issues "
            "-F sub_issue_id=91300: HTTP 502); run start again to link it\n"
        )
        assert self._start(tmp_path, monkeypatch, fake) == 0

        assert len(fake.created) == 1
        assert fake.sub_issues == [(EPIC, "sub_issue_id=91300")]

    def test_a_legacy_issue_still_gets_a_short_sub_issue(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeGh({EPIC: _workplan(_row("S1"), _row("S2", outcomes=[]))})

        assert self._start(tmp_path, monkeypatch, fake) == 0

        assert fake.created[0][1] == (
            f"Slice S2 of #{EPIC} (2 slices): Title S2. The work plan comment on #{EPIC} tracks it.\n\n"
            "## Why\nTitle S2.\n\n## Outcomes\n- Title S2.\n\n## Decisions\n- **D-2** Drop v1 now.\n\n"
            "## Done when\nTitle S2.\n"
        )


class ReadyTests:
    def _state(self, monkeypatch: pytest.MonkeyPatch, fake: FakeGh, body_sha: str = BODY_SHA) -> int:
        return _main(monkeypatch, fake, "ready-state", "--issue", str(EPIC), "--slice", "S1", "--body-sha", body_sha)

    @pytest.mark.parametrize(
        ("row", "labelled", "body_sha", "ready", "reason"),
        [
            pytest.param(
                _row("S1", ready_body_sha=BODY_SHA),
                True,
                BODY_SHA,
                "true",
                f"Issue {EPIC} is labelled dovo:ready for its description as it is; not checking it.",
                id="unchanged",
            ),
            pytest.param(
                _row("S1", ready_body_sha=BODY_SHA),
                False,
                BODY_SHA,
                "false",
                f"Issue {EPIC} is not labelled dovo:ready; checking it.",
                id="not-labelled",
            ),
            pytest.param(
                _row("S1"),
                True,
                BODY_SHA,
                "false",
                f"No ready check of issue {EPIC} is recorded in the work plan; checking it.",
                id="not-recorded",
            ),
            pytest.param(
                _row("S1", ready_body_sha=BODY_SHA),
                True,
                "0" * 64,
                "false",
                f"Issue {EPIC}'s description changed since the check that labelled it; checking it.",
                id="description-changed",
            ),
            pytest.param(
                _row("S1", ready_body_sha=BODY_SHA, child=1300),
                False,
                BODY_SHA,
                "false",
                "Issue 1300 is not labelled dovo:ready; checking it.",
                id="own-issue-not-labelled",
            ),
        ],
    )
    def test_ready_state_is_true_only_for_the_description_the_recorded_check_read(
        self,
        row: object,
        labelled: bool,
        body_sha: str,
        ready: str,
        reason: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        output = tmp_path / "output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output))
        fake = FakeGh({EPIC: OLD_READY_COMMENT})
        fake.comments[EPIC].append({"id": 7, "body": _workplan(row)})
        if labelled:
            fake.labels[EPIC] = ["dovo:ready"]

        assert self._state(monkeypatch, fake, body_sha) == 0

        assert output.read_text(encoding="utf-8") == f"ready={ready}\n"
        assert capsys.readouterr().out == f"{reason}\n"

    def test_ready_records_the_checked_description_on_the_slices_row(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGh({EPIC: _workplan(_row("S1"), _row("S2"))})

        argv = ("ready", "--issue", str(EPIC), "--slice", "S1", "--body-sha", BODY_SHA)
        assert _main(monkeypatch, fake, *argv) == 0

        assert [row["ready_body_sha"] for row in _rows(fake.workplan())] == [BODY_SHA, None]
        assert len(fake.texts(EPIC)) == 1

    def test_a_new_sub_issue_drops_the_ready_check_recorded_for_the_issue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGh({EPIC: _workplan(_row("S1", ready_body_sha=BODY_SHA), _row("S2"))}, {EPIC: PARENT_BODY})
        plan = _write_json(tmp_path / "plan.json", {"contract": CONTRACT, "slices": [], "decisions": []})

        assert _main(monkeypatch, fake, "start", "--issue", str(EPIC), "--slice", "S1", "--plan", plan) == 0

        first = _rows(fake.workplan())[0]
        assert (first["child"], first["ready_body_sha"]) == (1300, None)


class ShowTests:
    @pytest.mark.parametrize(
        ("comments", "outputs"),
        [
            pytest.param({}, "plan_session=\nbody_sha=\n", id="no-work-plan"),
            pytest.param(
                {EPIC: _workplan(_row("S1"), body_sha=BODY_SHA)},
                f"plan_session={PLAN_SESSION}\nbody_sha={BODY_SHA}\n",
                id="work-plan",
            ),
            pytest.param({EPIC: _workplan(_row("S1"))}, f"plan_session={PLAN_SESSION}\nbody_sha=\n", id="no-sha"),
        ],
    )
    def test_writes_the_plan_session_and_the_description_it_planned(
        self, comments: dict[int, str], outputs: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output = tmp_path / "output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output))

        assert _main(monkeypatch, FakeGh(comments), "show", "--issue", str(EPIC)) == 0

        assert output.read_text(encoding="utf-8") == outputs


class SplitOutTests:
    def _split(self, monkeypatch: pytest.MonkeyPatch, fake: FakeGh) -> int:
        return _main(monkeypatch, fake, "split-out", "--issue", str(EPIC), "--slice", "S2")

    def test_creates_links_and_records_the_slices_own_issue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output = tmp_path / "output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output))
        fake = FakeGh({EPIC: _workplan(_row("S1"), _row("S2", outcomes=["O-2", "O-3"], size="m"))})

        assert self._split(monkeypatch, fake) == 0

        assert fake.created == [
            (
                "Title S2",
                f"Slice S2 of #{EPIC} (2 slices), split out to its own issue.\n\n"
                f"Delivers O-2, O-3 of #{EPIC}. The work plan comment on #{EPIC} tracks it.\n",
            )
        ]
        assert fake.labels[1300] == []
        assert fake.sub_issues == [(EPIC, "sub_issue_id=91300")]
        assert [row["child"] for row in _rows(fake.workplan())] == [None, 1300]
        assert "| S2 | Title S2 | O-2, O-3 | — | pending | #1300 | — | — |" in fake.workplan()
        assert output.read_text(encoding="utf-8") == "child=1300\n"

    def test_a_link_failure_keeps_the_record_and_a_rerun_links_without_creating(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh({EPIC: _workplan(_row("S1"), _row("S2"))})
        fake.failing_links = 1

        assert self._split(monkeypatch, fake) == 1
        assert capsys.readouterr().err == (
            f"workplan.py: linking #1300 failed (gh api --method POST repos/o/r/issues/{EPIC}/sub_issues "
            "-F sub_issue_id=91300: HTTP 502); run split-out again to link it\n"
        )
        assert _rows(fake.workplan())[1]["child"] == 1300
        assert self._split(monkeypatch, fake) == 0

        assert len(fake.created) == 1
        assert fake.sub_issues == [(EPIC, "sub_issue_id=91300")]

    def test_handed_off_creates_the_issue_and_marks_the_slice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output = tmp_path / "output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output))
        fake = FakeGh({EPIC: _workplan(_row("S1"), _row("S2"))})

        assert _main(monkeypatch, fake, "split-out", "--issue", str(EPIC), "--slice", "S2", "--handed-off") == 0

        assert [(row["child"], row["status"]) for row in _rows(fake.workplan())] == [
            (None, "pending"),
            (1300, "handed_off"),
        ]
        assert "| S2 | Title S2 | O-1 | — | handed_off | #1300 | — | — |" in fake.workplan()
        assert fake.sub_issues == [(EPIC, "sub_issue_id=91300")]
        assert output.read_text(encoding="utf-8") == "child=1300\n"

    def test_handed_off_keeps_the_slices_existing_issue(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        output = tmp_path / "output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output))
        fake = FakeGh({EPIC: _workplan(_row("S1"), _row("S2", child=1131))})

        assert _main(monkeypatch, fake, "split-out", "--issue", str(EPIC), "--slice", "S2", "--handed-off") == 0

        assert [(row["child"], row["status"]) for row in _rows(fake.workplan())][1] == (1131, "handed_off")
        assert (fake.created, fake.sub_issues) == ([], [(EPIC, "sub_issue_id=91131")])
        assert output.read_text(encoding="utf-8") == "child=1131\n"

    def test_handed_off_refuses_a_slice_that_started(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh({EPIC: _workplan(_row("S1"), _row("S2", status="in_progress", session=CODE_SESSION))})

        assert _main(monkeypatch, fake, "split-out", "--issue", str(EPIC), "--slice", "S2", "--handed-off") == 1

        assert capsys.readouterr().err == "workplan.py: S2 is in_progress; only a pending slice can be handed off\n"
        assert (fake.created, fake.patched, fake.sub_issues) == ([], [], [])

    @pytest.mark.parametrize(
        ("extra", "hint"),
        [
            pytest.param((), "record #1300 as S2's child", id="split-out"),
            pytest.param(
                ("--handed-off",), "record #1300 as S2's child and mark the slice handed_off", id="handed-off"
            ),
        ],
    )
    def test_a_comment_changed_twice_refuses_naming_the_created_issue(
        self,
        extra: tuple[str, ...],
        hint: str,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rows = (_row("S1"), _row("S2"))
        fake = FakeGh({EPIC: _workplan(*rows)})
        fake.race[(EPIC, 3)] = _workplan(*rows, head="Once.\n")
        fake.race[(EPIC, 5)] = _workplan(*rows, head="Twice.\n")

        assert _main(monkeypatch, fake, "split-out", "--issue", str(EPIC), "--slice", "S2", *extra) == 1

        assert capsys.readouterr().err == (
            f"workplan.py: created #1300 but #{EPIC}'s work plan comment kept changing; {hint}\n"
        )
        assert (len(fake.created), fake.patched, fake.sub_issues) == (1, [], [])
        assert _rows(fake.workplan())[1]["child"] is None

    def test_unreadable_create_output_refuses_with_the_raw_output(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh({EPIC: _workplan(_row("S1"), _row("S2"))})
        fake.create_output = "created, somewhere"

        assert self._split(monkeypatch, fake) == 1

        assert capsys.readouterr().err == (
            "workplan.py: created a child issue for S2 but cannot read its number from gh's output "
            "'created, somewhere'; record it as S2's child by hand\n"
        )
        assert fake.patched == []


class PhasedTests:
    @pytest.mark.parametrize(
        ("row", "body_sha", "session", "reason"),
        [
            pytest.param(
                _row("S1", phased_session=PLAN_SESSION, phased_body_sha=BODY_SHA),
                BODY_SHA,
                PLAN_SESSION,
                f"S1 was phased and approved in session {PLAN_SESSION}; planning passes through.",
                id="holds",
            ),
            pytest.param(_row("S1"), BODY_SHA, "", "S1 has no phased plan; it is phased in this run.", id="not-phased"),
            pytest.param(
                _row("S1", phased_session=PLAN_SESSION, phased_body_sha=BODY_SHA, child=1300),
                BODY_SHA,
                "",
                "S1 is now worked on its own issue #1300, not the one it was phased for, "
                "so S1 is phased again in this run.",
                id="own-issue",
            ),
            pytest.param(
                _row("S1", phased_session="bp_older", phased_body_sha=BODY_SHA),
                BODY_SHA,
                "",
                f"the work plan now comes from session {PLAN_SESSION}, not bp_older, so S1 is phased again in this run.",
                id="plan-amended",
            ),
            pytest.param(
                _row("S1", phased_session=PLAN_SESSION, phased_body_sha=BODY_SHA),
                "0" * 64,
                "",
                "the issue's description changed since S1 was phased, so S1 is phased again in this run.",
                id="description-edited",
            ),
        ],
    )
    def test_writes_the_phased_session_only_while_the_phased_plan_holds(
        self,
        row: object,
        body_sha: str,
        session: str,
        reason: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        output = tmp_path / "output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output))
        fake = FakeGh({EPIC: _workplan(row)})

        argv = ("phased", "--issue", str(EPIC), "--slice", "S1", "--body-sha", body_sha)
        assert _main(monkeypatch, fake, *argv) == 0

        assert output.read_text(encoding="utf-8") == f"phased_session={session}\n"
        assert capsys.readouterr() == (f"{reason}\n", "")
        assert fake.patched == []


class PrLinksTests:
    DESCRIPTION = "## Why\nSlices need links.\n\nPart of #925 (slice S2 of 3)\n"

    def _links(self, monkeypatch: pytest.MonkeyPatch, fake: FakeGh, closing: str) -> int:
        return _main(monkeypatch, fake, "pr-links", "--pr", "1216", "--closing-lines", closing)

    @pytest.mark.parametrize(
        ("body", "closing", "after"),
        [
            pytest.param(
                DESCRIPTION, "Closes #925", "## Why\nSlices need links.\n\nCloses #925\n", id="last-slice-now"
            ),
            pytest.param(
                "## Why\nSlices need links.\r\nCloses #1131\r\nPart of #925 (slice S2 of 3)\r\n\r\n",
                "Closes #1131\nPart of #925 (slice S2 of 4)",
                "## Why\nSlices need links.\n\nCloses #1131\nPart of #925 (slice S2 of 4)\n",
                id="slice-added",
            ),
            pytest.param(
                "## Why\nSlices need links.\n",
                "Closes #925",
                "## Why\nSlices need links.\n\nCloses #925\n",
                id="missing",
            ),
            pytest.param(
                "## Why\nSlices need links.\n\nFixes #925\n",
                "Closes #925",
                "## Why\nSlices need links.\n\nCloses #925\n",
                id="fixes",
            ),
            pytest.param(
                "## Why\nSlices need links.\n\n  closes #925\n",
                "Part of #925 (slice S2 of 3)",
                "## Why\nSlices need links.\n\nPart of #925 (slice S2 of 3)\n",
                id="lowercase-closes",
            ),
            pytest.param(
                "## Why\nSlices need links.\n\nResolves: #1131\nPart of #925 (slice S2 of 3)\n",
                "Closes #1131\nPart of #925 (slice S2 of 3)",
                "## Why\nSlices need links.\n\nCloses #1131\nPart of #925 (slice S2 of 3)\n",
                id="resolves-with-colon",
            ),
            pytest.param(
                "## Why\nFixes #1000\n\n```text\nCloses #925\n```\n\nPart of #925 (slice S2 of 3)\n",
                "Closes #925",
                "## Why\nFixes #1000\n\n```text\nCloses #925\n```\n\nCloses #925\n",
                id="other-issue-and-code-block-kept",
            ),
        ],
    )
    def test_replaces_closing_lines_that_differ(
        self, body: str, closing: str, after: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh({})
        fake.pr_bodies[1216] = body

        assert self._links(monkeypatch, fake, closing) == 0

        assert (fake.pr_bodies[1216], fake.pr_edits) == (after, [1216])
        lines = "; ".join(closing.split("\n"))
        assert capsys.readouterr() == (f"#1216's description now ends with: {lines}.\n", "")

    def test_leaves_a_description_with_the_same_closing_lines_alone(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh({})
        fake.pr_bodies[1216] = self.DESCRIPTION

        assert self._links(monkeypatch, fake, "Part of #925 (slice S2 of 3)\n") == 0

        assert (fake.pr_bodies[1216], fake.pr_edits) == (self.DESCRIPTION, [])
        assert capsys.readouterr() == ("#1216's description already ends with its closing lines.\n", "")

    def test_reads_no_closing_line_in_a_code_block_or_naming_another_issue(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        body = (
            "## Why\nFixes #1000\n\n~~~~markdown\nCloses #925\n~~~\nPart of #925 (slice S1 of 3)\n~~~~\n\n"
            "Part of #925 (slice S2 of 3)\n"
        )
        fake = FakeGh({})
        fake.pr_bodies[1216] = body

        assert self._links(monkeypatch, fake, "Part of #925 (slice S2 of 3)") == 0

        assert (fake.pr_bodies[1216], fake.pr_edits) == (body, [])
        assert capsys.readouterr() == ("#1216's description already ends with its closing lines.\n", "")

    @pytest.mark.parametrize("closing", [pytest.param("", id="empty"), pytest.param("Fixes #925", id="not-closing")])
    def test_refuses_closing_lines_land_would_not_write(
        self, closing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh({})
        fake.pr_bodies[1216] = self.DESCRIPTION

        assert self._links(monkeypatch, fake, closing) == 1

        assert capsys.readouterr().err == (
            "workplan.py: --closing-lines must be lines such as `Closes #925` or `Part of #925 (slice S2 of 4)`, "
            f"not {closing!r}\n"
        )
        assert fake.pr_edits == []


@pytest.mark.parametrize(
    ("outputs", "text"),
    [
        pytest.param({"slice": "S1"}, "slice=S1\n", id="single-line"),
        pytest.param({"a": "x\ny"}, "a<<WORKPLAN_EOF\nx\ny\nWORKPLAN_EOF\n", id="multi-line"),
    ],
)
def test_output_lines(outputs: dict[str, str], text: str) -> None:
    assert WORKPLAN.output_lines(outputs) == text
