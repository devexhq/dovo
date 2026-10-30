"""Tier 1 contract: sdlc v2's workplan.py renders, picks, claims and lands an issue's slices in its work plan comment."""

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


def _workplan(*rows: object, landed: str = "", head: str = "") -> str:
    """Return a work plan comment holding head, then a work plan of rows from PLAN_SESSION revision 3."""
    plan = WORKPLAN.WorkPlan(PLAN_SESSION, 3, list(rows))
    return WORKPLAN.with_workplan(f"{MARKER}\n\n{head}" if head else f"{MARKER}\n", plan, landed)


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

CONTRACT_SECTION = """\
## Feature contract
Grounded at `main@944de0ce`.

### Problem
Output shapes disagree.

### Existing issues
| Issue | Role | Becomes |
|---|---|---|
| #1131 | child | S1 |

### Prerequisites
- #1192: needed by S2, because results carry errors

### Non-goals
- **Excluded:** Colour, because cosmetic.
- **Owned:** Locks, owned by #999.

### Target contract
- **JSON envelope** (`results.md#envelope`): one envelope
  - Baseline (main@944de0ce): two envelopes
  - Touched by: S1, S2.

### Conventions
- Use ok (new; written by S1)
- **Exemplar:** `src/a.py`, does not yet conform (fixed by S2). Shortest.

### Overrides
- #1060, "keep it" → drop it, per D1.

### Decisions
| ID | Decision | Why | Settled by |
|---|---|---|---|
| D1 | One envelope | Agreement | user 2026-10-28 |

Open questions, each with a recommended answer until the user settles it:
- **OQ-1** (blocks S2): Drop v1? (assumed: yes) **Recommended:** Yes.

### Code inventory
At `944de0ce`.
- envelopes: 2, S1 (`rg x`)

<details><summary>Sites</summary>

- `src/a.py:3`

</details>

### Shared-surface ownership
| Surface | Aspect | Owner | Extended by |
|---|---|---|---|
| envelope | create | S1 | S2 |

### Lint gates
| Check | Enforces | Lands with | Cites |
|---|---|---|---|
| `tests/lint/test_x.py` (new) | one envelope | S1 | — |

### Checkpoints
- After the pilot (S1).
- Baseline: before S2, across every landed slice and any open slice PR.
"""


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

        assert _render(monkeypatch, fake, plan, "2") == 0

        stored = (
            '{"plan_session":"blueprint_plan01","plan_revision":2,"slices":[{"id":"S1","title":"Title S1",'
            '"depends_on":[],"blocked_by":[],"closing_docs":false,"outcomes":["O-1"],"status":"pending","pr":null,'
            '"session":null,"size":"s","child":null,"phased_session":null}]}'
        )
        assert fake.texts(EPIC) == [
            f"{MARKER}\n"
            "\n"
            "## Work plan\n"
            "Plan Session ID: `blueprint_plan01` (plan revision 2)\n"
            "\n"
            "| Slice | Scope | Outcomes | Depends on | Status | PR | Code session |\n"
            "|---|---|---|---|---|---|---|\n"
            "| S1 | Title S1 | O-1 | — | pending | — | — |\n"
            "\n"
            f"<!-- dovo:workplan {stored} -->\n"
        ]
        assert (fake.bodies[EPIC], fake.edits, fake.created) == (INTRO, [], [])

    def test_epic_renders_contract_before_work_plan_and_a_second_render_edits_the_same_comment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        question = {"id": "OQ-1", "summary": "Drop v1? (assumed: yes)", "assumed": "Yes.", "blocks": ["S2"]}
        slices = [_slice("S1", pilot=True), _slice("S2", depends_on=["S1"], blocked_by=[1192], outcomes=["O-2"])]
        plan = _write_json(
            tmp_path / "plan.json", {"contract": CONTRACT, "slices": slices, "open_questions": [question]}
        )
        fake = FakeGh({})

        assert _render(monkeypatch, fake, plan, "1") == 0
        first = fake.workplan()
        assert _render(monkeypatch, fake, plan, "2") == 0

        assert first.startswith(f"{MARKER}\n\n{CONTRACT_SECTION}\n## Work plan\n")
        assert "| S2 | Title S2 | O-2 | S1, #1192 | pending | — | — |" in first
        assert fake.texts(EPIC) == [
            first.replace("(plan revision 1)", "(plan revision 2)").replace('"plan_revision":1', '"plan_revision":2')
        ]
        assert fake.patched == [6_513_400_000]
        assert (fake.bodies[EPIC], fake.edits) == (INTRO, [])

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

    def test_short_contract_renders_only_its_sections(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        empty: dict[str, object] = {
            key: [] for key in ("existing_issues", "prerequisites", "non_goals", "conventions", "exemplars")
        }
        empty.update({key: [] for key in ("overrides", "decisions", "lint_gates", "checkpoints")})
        contract = {
            **empty,
            "inventory": None,
            "problem": "Output shapes disagree.",
            "target": [
                {
                    "surface": "JSON envelope",
                    "entry": None,
                    "end_state": "one envelope",
                    "baseline": {"as_of": "main@944de0ce", "text": "two envelopes"},
                    "touched_by": ["S1"],
                }
            ],
            "ownership": [{"surface": "envelope", "aspect": "create", "owner": "S1", "extended_by": []}],
        }
        fake = FakeGh({})
        plan = _write_json(
            tmp_path / "plan.json", {"contract": contract, "slices": [_slice("S1")], "open_questions": []}
        )

        assert _render(monkeypatch, fake, plan) == 0

        assert fake.workplan().split("## Work plan\n")[0] == (
            f"{MARKER}\n"
            "\n"
            "## Feature contract\n"
            "\n"
            "### Problem\n"
            "Output shapes disagree.\n"
            "\n"
            "### Target contract\n"
            "- **JSON envelope**: one envelope\n"
            "  - Baseline (main@944de0ce): two envelopes\n"
            "  - Touched by: S1.\n"
            "\n"
            "### Shared-surface ownership\n"
            "| Surface | Aspect | Owner | Extended by |\n"
            "|---|---|---|---|\n"
            "| envelope | create | S1 | — |\n"
            "\n"
        )

    def test_multi_line_override_is_fenced_under_its_bullet(self) -> None:
        replacement = "Slice S3 of #925.\n\n## Goal\n\n````text\nliteral\n````\n"
        overrides = [{"issue": 1259, "quote": "## Goal …", "replacement": replacement, "per": "D2"}]

        section = WORKPLAN.render_contract({"overrides": overrides}, "")

        assert section == (
            "## Feature contract\n"
            "\n"
            "### Overrides\n"
            '- #1259, "## Goal …", per D2, becomes:\n'
            "  `````text\n"
            "  Slice S3 of #925.\n"
            "\n"
            "  ## Goal\n"
            "\n"
            "  ````text\n"
            "  literal\n"
            "  ````\n"
            "  `````\n"
        )

    def test_refuses_a_contract_that_renders_a_heading_outside_a_fence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        contract = {"problem": "Output shapes disagree.\n## Notes"}
        fake = FakeGh({})
        plan = _write_json(
            tmp_path / "plan.json", {"contract": contract, "slices": [_slice("S1")], "open_questions": []}
        )

        assert _render(monkeypatch, fake, plan) == 1

        assert capsys.readouterr().err == (
            "workplan.py: the contract renders a `## ` heading outside a code fence, which would end its section\n"
        )
        assert fake.texts(EPIC) == []

    def test_rerender_keeps_slice_state_children_and_landed_reports(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        landed = "### S1 as landed (#1215)\n- Departed."
        fake = FakeGh(
            {EPIC: _workplan(_row("S1", status="merged", pr=1215, session="bp_c", child=1131), landed=landed)}
        )
        plan = _write_json(tmp_path / "plan.json", {"contract": None, "slices": [_slice("S1")]})

        assert _render(monkeypatch, fake, plan, "4") == 0

        text = fake.workplan()
        assert _stored(text)["plan_revision"] == 4
        assert [(row["status"], row["pr"], row["child"]) for row in _rows(text)] == [("merged", 1215, 1131)]
        assert "| S1 (#1131) | Title S1 | O-1 | — | merged | #1215 | bp_c |" in text
        assert text.endswith(f"-->\n\n{landed}\n")

    def test_phased_records_the_one_slice_as_phased_in_this_session(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGh({})
        plan = _write_json(tmp_path / "plan.json", {"contract": None, "slices": [_slice("S1", phases=[1, 2])]})

        assert _render(monkeypatch, fake, plan, "5", "--phased") == 0

        assert [(row["phased_session"], row["status"]) for row in _rows(fake.workplan())] == [(PLAN_SESSION, "pending")]

    @pytest.mark.parametrize(
        ("slices", "reason"),
        [
            pytest.param(
                [_slice("S1"), _slice("S2", outcomes=["O-2"])],
                "--phased needs a plan with one slice, not 2",
                id="two-slices",
            ),
            pytest.param(
                [_slice("S1", phases=[])], "--phased needs slice S1 to list its phases in plan.json", id="no-phases"
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

        assert _render(monkeypatch, fake, plan, "5", "--phased") == 1

        assert capsys.readouterr().err == f"workplan.py: {reason}\n"
        assert fake.texts(EPIC) == []

    @pytest.mark.parametrize(
        ("status", "kept"),
        [pytest.param("pending", None, id="pending-drops-it"), pytest.param("in_progress", "bp_old", id="started")],
    )
    def test_rerender_without_phased_keeps_the_phased_plan_only_of_a_started_slice(
        self, status: str, kept: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGh({EPIC: _workplan(_row("S1", status=status, phased_session="bp_old"))})
        plan = _write_json(tmp_path / "plan.json", {"contract": None, "slices": [_slice("S1")]})

        assert _render(monkeypatch, fake, plan, "4") == 0

        assert [row["phased_session"] for row in _rows(fake.workplan())] == [kept]

    def test_records_an_existing_sub_issue_the_plan_maps_to_a_slice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGh({})
        slices = [_slice("S1", child=1131), _slice("S2", outcomes=["O-2"])]
        plan = _write_json(tmp_path / "plan.json", {"contract": CONTRACT, "slices": slices})

        assert _render(monkeypatch, fake, plan) == 0

        text = fake.workplan()
        assert [(row["child"], row["status"]) for row in _rows(text)] == [(1131, "pending"), (None, "pending")]
        assert "| S1 (#1131) | Title S1 | O-1 | — | pending | — | — |" in text
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
                f"slice=S1\nissue={EPIC}\nplan_session={PLAN_SESSION}\nplan_revision=3\nphased_session=\nremaining=0\n"
                f"closing_lines=Closes #{EPIC}\n",
                id="only-slice",
            ),
            pytest.param(
                [_row("S1", status="merged", pr=1215), _row("S2", depends_on=["S1"]), _row("S3")],
                f"slice=S2\nissue={EPIC}\nplan_session={PLAN_SESSION}\nplan_revision=3\nphased_session=\nremaining=1\n"
                f"closing_lines=Part of #{EPIC} (slice S2 of 3)\n",
                id="middle-slice",
            ),
            pytest.param(
                [_row("S1", status="merged", pr=1215), _row("S2", status="merged", pr=1216), _row("S3")],
                f"slice=S3\nissue={EPIC}\nplan_session={PLAN_SESSION}\nplan_revision=3\nphased_session=\nremaining=0\n"
                f"closing_lines=Closes #{EPIC}\n",
                id="last-slice",
            ),
            pytest.param(
                [_row("S1", child=1131), _row("S2")],
                f"slice=S1\nissue=1131\nplan_session={PLAN_SESSION}\nplan_revision=3\nphased_session=\nremaining=1\n"
                f"closing_lines<<WORKPLAN_EOF\nCloses #1131\nPart of #{EPIC} (slice S1 of 2)\nWORKPLAN_EOF\n",
                id="own-issue",
            ),
            pytest.param(
                [_row("S1", phased_session=PLAN_SESSION)],
                f"slice=S1\nissue={EPIC}\nplan_session={PLAN_SESSION}\nplan_revision=3\n"
                f"phased_session={PLAN_SESSION}\nremaining=0\nclosing_lines=Closes #{EPIC}\n",
                id="phased",
            ),
        ],
    )
    def test_writes_outputs_for_the_picked_slice_and_creates_no_issue(
        self, rows: list[object], outputs: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output = tmp_path / "output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output))
        fake = FakeGh({EPIC: _workplan(*rows)})

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

    @pytest.mark.parametrize(
        ("closed", "closing"),
        [
            pytest.param(False, f"remaining=1\nclosing_lines=Part of #{EPIC} (slice S2 of 2)\n", id="issue-open"),
            pytest.param(True, f"remaining=0\nclosing_lines=Closes #{EPIC}\n", id="issue-closed"),
        ],
    )
    def test_never_picks_a_handed_off_slice_and_counts_it_done_once_its_issue_closes(
        self, closed: bool, closing: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output = tmp_path / "output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output))
        fake = FakeGh({EPIC: _workplan(_row("S1", status="handed_off", child=1300), _row("S2"))})
        if closed:
            fake.closed.add(1300)

        assert _main(monkeypatch, fake, "next", "--issue", str(EPIC)) == 0

        assert output.read_text(encoding="utf-8") == (
            f"slice=S2\nissue={EPIC}\nplan_session={PLAN_SESSION}\nplan_revision=3\nphased_session=\n{closing}"
        )

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
        assert f"| S1 | Title S1 | O-1 | — | in_progress | — | {CODE_SESSION} |" in fake.workplan()
        assert fake.edits == []

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
    def _argv(self, tmp_path: Path, plan: Mapping[str, object], *extra: str, report: str = "") -> tuple[str, ...]:
        """Return land's argv for S1 with PR 1215, a report, plan and an epic plan holding CONTRACT under tmp_path."""
        report_path = tmp_path / "slice-report.md"
        report_path.write_text(report or "## Departures\n- Renamed a helper.\n", encoding="utf-8")
        plan_path = _write_json(tmp_path / "plan.json", plan)
        base_path = _write_json(tmp_path / "epic-plan.json", {"contract": CONTRACT, "slices": []})
        return (
            "land", "--issue", str(EPIC), "--slice", "S1", "--session", CODE_SESSION,
            "--pr", "1215", "--report", str(report_path), "--plan", plan_path, "--base-plan", base_path, *extra,
        )  # fmt: skip

    def _contract_workplan(self) -> str:
        """Return a work plan comment holding CONTRACT_SECTION and a work plan whose S1 this code session claimed."""
        rows = [_row("S1", status="in_progress", session=CODE_SESSION), _row("S2", outcomes=["O-2"])]
        return _workplan(*rows, head=CONTRACT_SECTION)

    def _slices(self, **s2: object) -> list[dict[str, object]]:
        return [_slice("S1"), _slice("S2", outcomes=["O-2"], **s2)]

    def test_marks_in_review_and_appends_the_landed_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output = tmp_path / "output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output))
        fake = FakeGh({EPIC: self._contract_workplan()})
        slice_question = {"id": "OQ-1", "summary": "Rename the helper? (assumed: no)", "assumed": "No."}
        plan = {"contract": CONTRACT, "slices": self._slices(), "open_questions": [slice_question]}

        assert _main(monkeypatch, fake, *self._argv(tmp_path, plan)) == 0

        text = fake.workplan()
        stored = _stored(text)
        assert (stored["plan_session"], stored["plan_revision"]) == (PLAN_SESSION, 3)
        assert (_rows(text)[0]["status"], _rows(text)[0]["pr"]) == ("in_review", 1215)
        assert text.startswith(f"{MARKER}\n\n{CONTRACT_SECTION}\n## Work plan\n")
        assert text.endswith("-->\n\n### S1 as landed (#1215)\n#### Departures\n- Renamed a helper.\n")
        assert output.read_text(encoding="utf-8") == f"remaining=1\nclosing_lines=Part of #{EPIC} (slice S1 of 2)\n"
        assert (fake.bodies[EPIC], fake.edits) == (INTRO, [])

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
        fake = FakeGh({EPIC: _workplan(*rows, head=CONTRACT_SECTION)})
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
        fake = FakeGh({EPIC: self._contract_workplan()})
        before = fake.workplan()
        plan = {"contract": CONTRACT, "slices": self._slices()}

        assert _main(monkeypatch, fake, *self._argv(tmp_path, plan, report="x" * 65_536)) == 1

        error = capsys.readouterr().err
        match = re.fullmatch(
            r"workplan\.py: the work plan comment would be (?P<size>[\d,]+) characters, over GitHub's 65,536; "
            r'shorten its "as landed" reports by hand and run the command again\n',
            error,
        )
        assert match is not None, error
        assert int(match["size"].replace(",", "")) > 65_536
        assert (fake.workplan(), fake.patched) == (before, [])

    def test_amended_contract_is_rerendered_keeping_the_epic_questions_and_repoints(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGh({EPIC: self._contract_workplan()})
        amended = {**CONTRACT, "conventions": [{"rule": "Use ok() everywhere", "entry": None, "new_in": "S1"}]}

        assert _main(monkeypatch, fake, *self._argv(tmp_path, {"contract": amended, "slices": self._slices()})) == 0

        text = fake.workplan()
        expected = CONTRACT_SECTION.replace("- Use ok (new;", "- Use ok() everywhere (new;")
        assert text.startswith(f"{MARKER}\n\n{expected}\n## Work plan\n")
        stored = _stored(text)
        assert (stored["plan_session"], stored["plan_revision"]) == (CODE_SESSION, None)

    def test_a_contract_already_amended_is_left_alone(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        amended = {**CONTRACT, "conventions": [{"rule": "Use ok() everywhere", "entry": None, "new_in": "S1"}]}
        text = self._contract_workplan().replace("- Use ok (new;", "- Use ok() everywhere (new;")
        fake = FakeGh({EPIC: text})

        assert _main(monkeypatch, fake, *self._argv(tmp_path, {"contract": amended, "slices": self._slices()})) == 0

        assert fake.workplan().split("## Work plan")[0] == text.split("## Work plan")[0]
        assert _stored(fake.workplan())["plan_session"] == PLAN_SESSION

    def test_refuses_a_contract_edited_during_the_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh({EPIC: self._contract_workplan().replace("Output shapes disagree.", "Edited on GitHub.")})
        amended = {**CONTRACT, "conventions": [{"rule": "Use ok() everywhere", "entry": None, "new_in": "S1"}]}

        assert _main(monkeypatch, fake, *self._argv(tmp_path, {"contract": amended, "slices": self._slices()})) == 1

        assert capsys.readouterr().err == (
            f"workplan.py: #{EPIC}'s Feature contract was edited during this run: it is neither the contract this "
            "run started from nor the amended one; reconcile it by hand and run land again\n"
        )
        assert fake.patched == []

    def test_amends_a_contract_whose_override_replaces_a_whole_body(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        override = {
            "issue": 1259,
            "quote": "## Goal …",
            "replacement": "Slice S3.\n\n## Goal\n\nNew body.\n",
            "per": "D1",
        }
        base = {**CONTRACT, "overrides": [override]}
        amended = {**base, "conventions": [{"rule": "Use ok() everywhere", "entry": None, "new_in": "S1"}]}
        questions = WORKPLAN.body_questions(self._contract_workplan())
        rows = [_row("S1", status="in_progress", session=CODE_SESSION), _row("S2", outcomes=["O-2"])]
        fake = FakeGh({EPIC: _workplan(*rows, head=WORKPLAN.render_contract(base, questions))})
        argv = self._argv(tmp_path, {"contract": amended, "slices": self._slices()})
        _write_json(tmp_path / "epic-plan.json", {"contract": base, "slices": []})

        assert _main(monkeypatch, fake, *argv) == 0

        expected = WORKPLAN.render_contract(amended, questions)
        assert fake.workplan().startswith(f"{MARKER}\n\n{expected}\n## Work plan\n")

    def test_a_decision_with_an_open_questions_id_settles_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGh({EPIC: self._contract_workplan()})
        settled = {"id": "OQ-1", "decision": "Drop v1", "why": "S1 landed without it", "settled_by": None}
        decisions = CONTRACT["decisions"]
        assert isinstance(decisions, list)
        decided = {**CONTRACT, "decisions": [*decisions, settled]}

        assert _main(monkeypatch, fake, *self._argv(tmp_path, {"contract": decided, "slices": self._slices()})) == 0

        text = fake.workplan()
        assert "| OQ-1 | Drop v1 | S1 landed without it |" in text
        assert "Open questions, each with a recommended answer" not in text
        assert _stored(text)["plan_session"] == CODE_SESSION

    def test_crlf_comment_with_an_unchanged_contract_is_not_amended(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGh({EPIC: self._contract_workplan().replace("\n", "\r\n")})

        assert _main(monkeypatch, fake, *self._argv(tmp_path, {"contract": CONTRACT, "slices": self._slices()})) == 0

        assert _stored(fake.workplan())["plan_session"] == PLAN_SESSION
        assert WORKPLAN.lf(fake.workplan()).startswith(f"{MARKER}\n\n{CONTRACT_SECTION}\n## Work plan\n")

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
        fake = FakeGh({EPIC: self._contract_workplan()})
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
        assert fake.labels[1300] == ["dovo-size-m"]
        assert fake.sub_issues == [(EPIC, "sub_issue_id=91300")]
        assert [row["child"] for row in _rows(fake.workplan())] == [None, 1300]
        assert "| S2 (#1300) | Title S2 | O-2, O-3 | — | pending | — | — |" in fake.workplan()
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
        assert "| S2 (#1300) | Title S2 | O-1 | — | handed_off | — | — |" in fake.workplan()
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


class SizeLabelTests:
    @pytest.mark.parametrize(
        ("row", "labels", "issue", "after", "removed"),
        [
            pytest.param(
                _row("S2", size="m"),
                ["type:epic", "dovo-size-l"],
                EPIC,
                ["type:epic", "dovo-size-m"],
                [(EPIC, "dovo-size-l")],
                id="worked-on-the-issue",
            ),
            pytest.param(_row("S2", size="s", child=1300), [], 1300, ["dovo-size-s"], [], id="own-issue"),
            pytest.param(_row("S2", size="m"), ["dovo-size-m"], EPIC, ["dovo-size-m"], [], id="already-labelled"),
        ],
    )
    def test_labels_the_issue_the_slice_is_worked_on_with_its_size_alone(
        self,
        row: object,
        labels: list[str],
        issue: int,
        after: list[str],
        removed: list[tuple[int, str]],
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake = FakeGh({EPIC: _workplan(_row("S1"), row)})
        fake.labels[issue] = labels

        assert _main(monkeypatch, fake, "size-label", "--issue", str(EPIC), "--slice", "S2") == 0

        assert (fake.labels[issue], fake.removed_labels) == (after, removed)
        assert capsys.readouterr() == ("", "")

    def test_a_slice_without_a_size_leaves_the_labels_and_says_so(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh({EPIC: _workplan(_row("S1", size=None))})
        fake.labels[EPIC] = ["dovo-size-l"]

        assert _main(monkeypatch, fake, "size-label", "--issue", str(EPIC), "--slice", "S1") == 0

        assert fake.labels[EPIC] == ["dovo-size-l"]
        assert capsys.readouterr().err == (
            f"workplan.py: S1 has no size in the work plan, so #{EPIC} gets no dovo-size-* label; label it by hand\n"
        )


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
