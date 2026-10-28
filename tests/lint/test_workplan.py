"""Tier 1 contract: scripts/workplan.py renders, picks, claims and lands an epic's slices on its issue body."""

from __future__ import annotations

import hashlib
import importlib.util
import json
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
INTRO = "Intro paragraph the script must keep.\n"


def _load_script() -> ModuleType:
    """Load scripts/workplan.py by path, since scripts/ is not a package."""
    spec = importlib.util.spec_from_file_location("workplan_under_test", REPO_ROOT / "scripts" / "workplan.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["workplan_under_test"] = module
    spec.loader.exec_module(module)
    return module


WORKPLAN = _load_script()


class FakeGh:
    """A `run` double answering the gh calls workplan.py makes, against in-memory issues and pull requests.

    It has `run`'s signature, so it replaces it at the subprocess boundary. `race[(issue, n)]` is a body
    another writer stores right after the n-th read of that issue, to exercise compare-and-set.
    `failing_links` makes that many sub-issue POSTs fail, and `create_output` replaces what
    `gh issue create` prints.
    """

    def __init__(self, bodies: dict[int, str]) -> None:
        self.bodies = dict(bodies)
        self.labels: dict[int, list[str]] = {}
        self.closed: set[int] = set()
        self.pr_states: dict[int, str] = {}
        self.race: dict[tuple[int, int], str] = {}
        self.reads: dict[int, int] = {}
        self.edits: list[int] = []
        self.comments: list[tuple[int, str]] = []
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
            return json.dumps({"state": self.pr_states[int(args[3])]})
        if args[:3] == ("gh", "issue", "edit"):
            return self._edit(int(args[3]), args[4], args[5])
        if args[:3] == ("gh", "issue", "comment"):
            self.comments.append((int(args[3]), Path(args[5]).read_text(encoding="utf-8")))
            return ""
        if args[:3] == ("gh", "issue", "create"):
            return self._create(args[4], Path(args[6]).read_text(encoding="utf-8"))
        if args[:3] == ("gh", "repo", "view"):
            return "o/r"
        assert args[:2] == ("gh", "api"), f"ran {args}"
        return self._api(args)

    def _view(self, issue: int, fields: str) -> str:
        self.reads[issue] = self.reads.get(issue, 0) + 1
        if (issue, self.reads[issue]) in self.race:
            self.bodies[issue] = self.race.pop((issue, self.reads[issue]))
        view: dict[str, object] = {
            "body": self.bodies.get(issue, ""),
            "state": "CLOSED" if issue in self.closed else "OPEN",
        }
        view["labels"] = [{"name": name} for name in self.labels.get(issue, [])]
        return json.dumps({field: view[field] for field in fields.split(",")})

    def _edit(self, issue: int, flag: str, value: str) -> str:
        if flag == "--remove-label":
            self.removed_labels.append((issue, value))
            return ""
        assert flag == "--body-file"
        self.bodies[issue] = Path(value).read_text(encoding="utf-8")
        self.edits.append(issue)
        return ""

    def _create(self, title: str, body: str) -> str:
        number = self.next_number
        self.next_number += 1
        self.bodies[number] = body
        self.created.append((title, body))
        return self.create_output or f"Creating issue in o/r\n\nhttps://github.com/o/r/issues/{number}"

    def _api(self, args: tuple[str, ...]) -> str:
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
    """Return a plan.json slice with slice_id and overrides, whose child issue is still to be created."""
    data: dict[str, object] = {
        "id": slice_id,
        "title": f"Title {slice_id}",
        "phases": [1],
        "depends_on": [],
        "blocked_by": [],
        "child": None,
        "owns": [],
        "lint_gates": [],
        "pilot": False,
        "closing_docs": False,
        "estimate": {"src_docs": 100, "tests": 100, "basis": "x"},
        "stub": f"Stub for {slice_id}.",
    }
    data.update(overrides)
    return data


def _row(slice_id: str, **overrides: object) -> object:
    """Return a stored work plan slice row with slice_id and overrides."""
    defaults: dict[str, object] = {
        "id": slice_id,
        "title": f"Title {slice_id}",
        "depends_on": [],
        "blocked_by": [],
        "closing_docs": False,
        "child": None,
        "stub": None,
    }
    defaults.update(overrides)
    return WORKPLAN.Slice(**defaults)


def _epic_body(*rows: object, landed: str = "") -> str:
    """Return an epic body holding INTRO and a work plan of rows from PLAN_SESSION revision 3."""
    return WORKPLAN.with_workplan(INTRO, WORKPLAN.WorkPlan(PLAN_SESSION, 3, list(rows)), landed)


def _stored(body: str) -> dict[str, object]:
    """Return the dovo:workplan JSON stored in body."""
    match = WORKPLAN.WORKPLAN_COMMENT_RE.search(body)
    assert match is not None
    return json.loads(match.group(1))


def _rows(body: str) -> list[dict[str, object]]:
    """Return the slice rows of the dovo:workplan JSON stored in body."""
    rows = _stored(body)["slices"]
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


class RenderTests:
    def test_plain_issue_renders_one_slice_whose_child_is_the_issue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGh({42: INTRO})
        plan = _write_json(tmp_path / "plan.json", {"contract": None, "slices": [_slice("S1", stub=None)]})

        code = _main(
            monkeypatch, fake, "render", "--issue", "42", "--plan", plan, "--session", "bp_1", "--revision", "2"
        )

        assert code == 0
        stored = (
            '{"plan_session":"bp_1","plan_revision":2,"slices":[{"id":"S1","title":"Title S1","depends_on":[],'
            '"blocked_by":[],"closing_docs":false,"child":42,"stub":null,"status":"pending","pr":null,"session":null}]}'
        )
        assert fake.bodies[42] == (
            f"{INTRO}\n"
            "## Work plan\n"
            "Plan Session ID: `bp_1` (plan revision 2)\n"
            "\n"
            "| Slice | Scope | Child | Depends on | Status | PR | Code session |\n"
            "|---|---|---|---|---|---|---|\n"
            "| S1 | Title S1 | #42 | — | pending | — | — |\n"
            "\n"
            f"<!-- dovo:workplan {stored} -->\n"
        )

    def test_epic_renders_contract_before_work_plan_and_rerenders_in_place(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        question = {"id": "OQ-1", "summary": "Drop v1? (assumed: yes)", "assumed": "Yes.", "blocks": ["S2"]}
        slices = [_slice("S1", child=1131, pilot=True), _slice("S2", depends_on=["S1"], blocked_by=[1192])]
        plan = {"contract": CONTRACT, "slices": slices, "open_questions": [question]}
        plan_path = _write_json(tmp_path / "plan.json", plan)
        fake = FakeGh({EPIC: f"{INTRO}\n## Notes\nKept.\n"})
        argv = ("render", "--issue", str(EPIC), "--plan", plan_path, "--session", PLAN_SESSION, "--revision", "1")

        assert _main(monkeypatch, fake, *argv) == 0
        first = fake.bodies[EPIC]
        assert _main(monkeypatch, fake, *argv) == 0

        assert fake.bodies[EPIC] == first
        assert first.startswith(f"{INTRO}\n## Notes\nKept.\n\n{CONTRACT_SECTION}\n## Work plan\n")
        assert "| S2 | Title S2 | — | S1, #1192 | pending | — | — |" in first
        assert [(row["id"], row["child"], row["stub"]) for row in _rows(first)] == [
            ("S1", 1131, None),
            ("S2", None, "Stub for S2."),
        ]

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
        plan = {"contract": contract, "slices": [_slice("S1", child=1131)], "open_questions": []}
        fake = FakeGh({EPIC: ""})
        path = _write_json(tmp_path / "plan.json", plan)

        code = _main(
            monkeypatch, fake, "render", "--issue", str(EPIC), "--plan", path, "--session", "s", "--revision", "1"
        )

        assert code == 0
        assert fake.bodies[EPIC].split("## Work plan\n")[0] == (
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

    def test_rerender_keeps_slice_state_and_landed_reports(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        landed = "### S1 as landed (#1215)\n- Departed."
        body = _epic_body(_row("S1", child=1131, status="merged", pr=1215, session="bp_c"), landed=landed)
        fake = FakeGh({EPIC: body})
        plan = _write_json(tmp_path / "plan.json", {"contract": None, "slices": [_slice("S1", child=1131)]})

        code = _main(
            monkeypatch, fake, "render", "--issue", str(EPIC), "--plan", plan, "--session", "bp_2", "--revision", "4"
        )

        assert code == 0
        assert _stored(fake.bodies[EPIC])["plan_session"] == "bp_2"
        assert _rows(fake.bodies[EPIC])[0]["status"] == "merged"
        assert fake.bodies[EPIC].endswith(f"-->\n\n{landed}\n")

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
                {"contract": CONTRACT, "slices": [_slice("S1", stub=None)]},
                "slice S1 has neither a child issue nor a stub to create one from",
                id="no-stub",
            ),
            pytest.param(
                {"contract": CONTRACT, "slices": [_slice("S1", depends_on=["S2"]), _slice("S2")]},
                "slice S1 depends on S2, which is not an earlier slice",
                id="forward-dependency",
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
        fake = FakeGh({EPIC: INTRO})
        path = _write_json(tmp_path / "plan.json", plan)

        code = _main(
            monkeypatch, fake, "render", "--issue", str(EPIC), "--plan", path, "--session", "s", "--revision", "1"
        )

        assert code == 1
        assert capsys.readouterr().err == f"workplan.py: {reason}\n"
        assert fake.edits == []

    def test_aborts_when_body_changes_after_it_was_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh({42: INTRO})
        fake.race[(42, 2)] = "Someone else's edit.\n"
        plan = _write_json(tmp_path / "plan.json", {"contract": None, "slices": [_slice("S1")]})

        code = _main(monkeypatch, fake, "render", "--issue", "42", "--plan", plan, "--session", "s", "--revision", "1")

        assert code == 1
        assert (
            capsys.readouterr().err == "workplan.py: #42's body changed since this run read it; run the command again\n"
        )
        assert fake.bodies[42] == "Someone else's edit.\n"
        assert fake.edits == []


# An epic body in the shape of #928's before the work plan script existed: a hand-written contract and a
# hand-maintained `## Work plan` with prose, a table and no dovo:workplan comment, followed by another section.
HAND_WORKPLAN = """\
## Work plan

Maintained by hand until `workplan.py` exists.

| Slice | Scope | Child issue | Depends on | Blocked by | Status |
|---|---|---|---|---|---|
| A1 | `dovo init` to summaries; the code names (pilot) | #1112 | — | — | pending |
| A2 | Config tier loading raises `ConfigError` | #1118 | A1 | #1192 | merged |
| A7 | Closing docs review | created when next | A1, A2 | — | pending |

A2 depends on A1 only for its conventions.
- #925: S1 is blocked by A2.

### A2 as landed (#1301)
- Departed.
"""
HAND_TAIL = "\n## References\n\n- Kept.\n"


def _hand_body(workplan: str = HAND_WORKPLAN) -> str:
    """Return INTRO, a hand-written contract, workplan and HAND_TAIL, as one body."""
    return f"{INTRO}\n## Feature contract\nHand-written contract.\n\n{workplan}{HAND_TAIL}"


class AdoptTests:
    def _render(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: FakeGh, ids: tuple[str, ...]) -> int:
        """Render a plan of slices with ids, each with a stub and no child, into EPIC's body."""
        plan = {"contract": CONTRACT, "slices": [_slice(slice_id) for slice_id in ids], "open_questions": []}
        path = _write_json(tmp_path / "plan.json", plan)
        argv = ("render", "--issue", str(EPIC), "--plan", path, "--session", PLAN_SESSION, "--revision", "1")
        return _main(monkeypatch, fake, *argv)

    def test_replaces_the_hand_section_and_the_contract_and_nothing_else(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGh({EPIC: _hand_body()})

        assert self._render(tmp_path, monkeypatch, fake, ("A1", "A2", "A7")) == 0

        body = fake.bodies[EPIC]
        contract = WORKPLAN.render_contract(CONTRACT, "")
        assert body.startswith(
            f"{INTRO}\n{contract}\n## Work plan\nPlan Session ID: `{PLAN_SESSION}` (plan revision 1)\n"
        )
        assert body.endswith(
            "-->\n\n"
            "### Notes (carried over)\n"
            "Maintained by hand until `workplan.py` exists.\n"
            "\n"
            "A2 depends on A1 only for its conventions.\n"
            "- #925: S1 is blocked by A2.\n"
            "\n"
            "### A2 as landed (#1301)\n"
            "- Departed.\n" + HAND_TAIL
        )
        assert "Hand-written contract." not in body
        assert body.count("| A1 |") == 1

    def test_a_later_render_keeps_the_carried_over_notes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeGh({EPIC: _hand_body()})
        assert self._render(tmp_path, monkeypatch, fake, ("A1", "A2", "A7")) == 0
        adopted = fake.bodies[EPIC]

        assert self._render(tmp_path, monkeypatch, fake, ("A1", "A2", "A7")) == 0

        assert fake.bodies[EPIC] == adopted
        assert fake.bodies[EPIC].count("### Notes (carried over)") == 1

    def test_carries_over_each_rows_child_and_status(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeGh({EPIC: _hand_body()})

        assert self._render(tmp_path, monkeypatch, fake, ("A1", "A2", "A3", "A7")) == 0

        assert [(row["id"], row["child"], row["stub"], row["status"]) for row in _rows(fake.bodies[EPIC])] == [
            ("A1", 1112, None, "pending"),
            ("A2", 1118, None, "merged"),
            ("A3", None, "Stub for A3.", "pending"),
            ("A7", None, "Stub for A7.", "pending"),
        ]

    @pytest.mark.parametrize(
        ("workplan", "ids", "reason"),
        [
            pytest.param(
                HAND_WORKPLAN,
                ("S1", "S2", "S7"),
                "the hand-maintained work plan's row A1, A2, A7 matches no plan.json slice; "
                "give the slice that id or remove the row",
                id="unmatched-row",
            ),
            pytest.param(
                HAND_WORKPLAN.replace("| merged |", "| in progress |"),
                ("A1", "A2", "A7"),
                "the hand-maintained work plan's row A2 has status 'in progress', not pending or merged",
                id="unsafe-status",
            ),
            pytest.param(
                HAND_WORKPLAN.replace("| #1118 |", "| #1118, #1119 |"),
                ("A1", "A2", "A7"),
                "the hand-maintained work plan's row A2 has child '#1118, #1119', "
                'which is not one `#n`, empty or "created when next"',
                id="two-children",
            ),
            pytest.param(
                HAND_WORKPLAN.replace("| #1118 |", "| 1118 |"),
                ("A1", "A2", "A7"),
                "the hand-maintained work plan's row A2 has child '1118', "
                'which is not one `#n`, empty or "created when next"',
                id="child-without-hash",
            ),
            pytest.param(
                HAND_WORKPLAN.replace(" | #1118 | A1 | #1192 | merged |", " | #1118 |"),
                ("A1", "A2", "A7"),
                "the hand-maintained work plan's row A2 has 3 cells, fewer than the header's 6",
                id="short-row",
            ),
            pytest.param(
                HAND_WORKPLAN.replace("| A7 |", "| A1 |"),
                ("A1", "A2", "A7"),
                "the hand-maintained work plan's row A1 appears twice",
                id="duplicate-id",
            ),
            pytest.param(
                HAND_WORKPLAN.replace("| Child issue |", "| Issue |"),
                ("A1", "A2", "A7"),
                "the hand-maintained work plan's table has no `Child` or `Child issue` column",
                id="no-child-column",
            ),
        ],
    )
    def test_refuses_a_row_it_cannot_carry_over_safely(
        self,
        workplan: str,
        ids: tuple[str, ...],
        reason: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake = FakeGh({EPIC: _hand_body(workplan)})

        assert self._render(tmp_path, monkeypatch, fake, ids) == 1

        assert capsys.readouterr().err == f"workplan.py: {reason}\n"
        assert fake.edits == []

    @pytest.mark.parametrize(
        ("body", "ids", "rows", "kept"),
        [
            pytest.param(
                _hand_body("## Work plan\n\nSlices to come.\n"),
                ("A1",),
                [("A1", None, "pending")],
                "-->\n\n### Notes (carried over)\nSlices to come.\n",
                id="no-table",
            ),
            pytest.param(
                _hand_body(
                    "## Work plan\n\n| Slice | Child issue | Status |\n|---|---|---|\n| A1 | #1112 | pending |\n\n"
                    "Legend:\n\n| Key | Meaning |\n|---|---|\n| M | medium |\n"
                ),
                ("A1",),
                [("A1", 1112, "pending")],
                "-->\n\n### Notes (carried over)\nLegend:\n\n| Key | Meaning |\n|---|---|\n| M | medium |\n",
                id="second-table-kept-as-notes",
            ),
            pytest.param(
                _hand_body(
                    "## Work plan\n\n| Slice | Scope | Child | Size | Owner | Status |\n|---|---|---|---|---|---|\n"
                    "| A1 | x | #1112 | M | me | merged |\n"
                ),
                ("A1",),
                [("A1", 1112, "merged")],
                "-->\n",
                id="extra-columns",
            ),
            pytest.param(
                _hand_body().replace("\n", "\r\n"),
                ("A1", "A2", "A7"),
                [("A1", 1112, "pending"), ("A2", 1118, "merged"), ("A7", None, "pending")],
                "-->\n\n### Notes (carried over)\nMaintained by hand until `workplan.py` exists.\n\n"
                "A2 depends on A1 only for its conventions.\n- #925: S1 is blocked by A2.\n\n"
                "### A2 as landed (#1301)\n- Departed.\n",
                id="crlf",
            ),
        ],
    )
    def test_adopts_other_table_shapes(
        self,
        body: str,
        ids: tuple[str, ...],
        rows: list[tuple[str, int | None, str]],
        kept: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake = FakeGh({EPIC: body})

        assert self._render(tmp_path, monkeypatch, fake, ids) == 0

        new = fake.bodies[EPIC]
        assert [(row["id"], row["child"], row["status"]) for row in _rows(new)] == rows
        span = WORKPLAN.find_section(new, "## Work plan")
        assert span is not None
        assert new[span[0] : span[1]].endswith(kept + "\n")


class NextTests:
    @pytest.mark.parametrize(
        ("rows", "picked", "closing"),
        [
            pytest.param(
                [_row("S1", child=EPIC)],
                f"slice=S1\nchild={EPIC}\n",
                f"closing_lines=Fixes #{EPIC}\n",
                id="plain-issue",
            ),
            pytest.param(
                [_row("S1", child=1131, status="merged", pr=1215), _row("S2", child=1132, depends_on=["S1"])],
                "slice=S2\nchild=1132\n",
                f"closing_lines<<WORKPLAN_EOF\nFixes #1132\nRefs #{EPIC}\nWORKPLAN_EOF\n",
                id="middle-slice",
            ),
            pytest.param(
                [_row("S1", child=1131, status="merged", pr=1215), _row("S2", child=1132, closing_docs=True)],
                "slice=S2\nchild=1132\n",
                f"closing_lines<<WORKPLAN_EOF\nFixes #1132\nFixes #{EPIC}\nWORKPLAN_EOF\n",
                id="closing-docs-slice",
            ),
        ],
    )
    def test_writes_outputs_for_the_picked_slice(
        self,
        rows: list[object],
        picked: str,
        closing: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        output = tmp_path / "output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output))
        fake = FakeGh({EPIC: _epic_body(*rows)})

        assert _main(monkeypatch, fake, "next", "--issue", str(EPIC)) == 0

        assert output.read_text(encoding="utf-8") == (
            f"{picked}plan_session={PLAN_SESSION}\nplan_revision=3\n{closing}"
        )
        assert fake.edits == []

    def test_marks_merged_pr_and_creates_the_child_as_a_sub_issue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output = tmp_path / "output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output))
        rows = [
            _row("S1", child=1131, status="in_review", pr=1215, session="bp_c"),
            _row("S2", depends_on=["S1"], blocked_by=[1192], stub="Stub for S2."),
        ]
        fake = FakeGh({EPIC: _epic_body(*rows)})
        fake.pr_states[1215] = "MERGED"
        fake.closed.add(1192)

        assert _main(monkeypatch, fake, "next", "--issue", str(EPIC)) == 0

        assert fake.created == [("Title S2", "Stub for S2.")]
        assert fake.sub_issues == [(EPIC, "sub_issue_id=91300")]
        stored = _rows(fake.bodies[EPIC])
        assert [(row["id"], row["status"], row["child"], row["stub"]) for row in stored] == [
            ("S1", "merged", 1131, None),
            ("S2", "pending", 1300, None),
        ]
        assert output.read_text(encoding="utf-8").splitlines()[:2] == ["slice=S2", "child=1300"]

    @pytest.mark.parametrize(
        ("rows", "reason"),
        [
            pytest.param(
                [_row("S1", child=1131, status="in_review", pr=1215), _row("S2", child=1132)],
                "S1 is in_review (#1215); finish it before starting another slice",
                id="active-slice",
            ),
            pytest.param(
                [_row("S1", child=1131, depends_on=[], blocked_by=[1192]), _row("S2", child=1, depends_on=["S1"])],
                "no slice is ready: S1 is blocked by open #1192; S2 waits on S1",
                id="none-ready",
            ),
            pytest.param(
                [_row("S1", child=1131, status="merged", pr=1215)],
                "every slice is merged",
                id="all-merged",
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
        fake = FakeGh({EPIC: _epic_body(*rows)})
        fake.pr_states[1215] = "OPEN"

        assert _main(monkeypatch, fake, "next", "--issue", str(EPIC)) == 1

        assert capsys.readouterr().err == f"workplan.py: {reason}\n"
        assert not output.exists()
        assert fake.created == []

    def test_refuses_an_issue_without_a_work_plan(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh({EPIC: INTRO})

        assert _main(monkeypatch, fake, "next", "--issue", str(EPIC)) == 1

        assert capsys.readouterr().err == "workplan.py: the issue has no work plan; run `workplan.py render` first\n"

    def test_link_failure_records_the_child_and_a_rerun_links_it_without_creating(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh({EPIC: _epic_body(_row("S1", stub="Stub for S1."))})
        fake.failing_links = 1

        assert _main(monkeypatch, fake, "next", "--issue", str(EPIC)) == 1
        assert capsys.readouterr().err == (
            f"workplan.py: linking #1300 failed (gh api --method POST repos/o/r/issues/{EPIC}/sub_issues "
            "-F sub_issue_id=91300: HTTP 502); run next again to link it\n"
        )
        assert _rows(fake.bodies[EPIC])[0]["child"] == 1300
        assert _main(monkeypatch, fake, "next", "--issue", str(EPIC)) == 0

        assert len(fake.created) == 1
        assert fake.sub_issues == [(EPIC, "sub_issue_id=91300")]

    def test_an_already_linked_child_is_not_linked_again(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeGh({EPIC: _epic_body(_row("S1", child=1131))})
        fake.sub_issues.append((EPIC, "sub_issue_id=91131"))

        assert _main(monkeypatch, fake, "next", "--issue", str(EPIC)) == 0

        assert fake.sub_issues == [(EPIC, "sub_issue_id=91131")]
        assert fake.created == []

    def test_record_conflict_is_retried_on_the_new_body(self, monkeypatch: pytest.MonkeyPatch) -> None:
        body = _epic_body(_row("S1", stub="Stub for S1."))
        fake = FakeGh({EPIC: body})
        fake.race[(EPIC, 4)] = f"Edited meanwhile.\n{body}"

        assert _main(monkeypatch, fake, "next", "--issue", str(EPIC)) == 0

        assert fake.bodies[EPIC].startswith("Edited meanwhile.\n")
        assert _rows(fake.bodies[EPIC])[0]["child"] == 1300
        assert fake.sub_issues == [(EPIC, "sub_issue_id=91300")]

    def test_record_conflict_twice_refuses_naming_the_created_child(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        body = _epic_body(_row("S1", stub="Stub for S1."))
        fake = FakeGh({EPIC: body})
        fake.race[(EPIC, 4)] = f"Edited once.\n{body}"
        fake.race[(EPIC, 6)] = f"Edited twice.\n{body}"

        assert _main(monkeypatch, fake, "next", "--issue", str(EPIC)) == 1

        assert capsys.readouterr().err == (
            f"workplan.py: created #1300 but #{EPIC}'s body kept changing; record #1300 as S1's child\n"
        )
        assert fake.sub_issues == []

    def test_unreadable_create_output_refuses_with_the_raw_output(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh({EPIC: _epic_body(_row("S1", stub="Stub for S1."))})
        fake.create_output = "created, somewhere"

        assert _main(monkeypatch, fake, "next", "--issue", str(EPIC)) == 1

        assert capsys.readouterr().err == (
            "workplan.py: created a child issue for S1 but cannot read its number from gh's output "
            "'created, somewhere'; record it as S1's child by hand\n"
        )
        assert fake.edits == []


class ClaimTests:
    def test_marks_the_slice_in_progress_with_the_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeGh({EPIC: _epic_body(_row("S1", child=1131))})

        code = _main(monkeypatch, fake, "claim", "--issue", str(EPIC), "--slice", "S1", "--session", CODE_SESSION)

        assert code == 0
        row = _rows(fake.bodies[EPIC])[0]
        assert (row["status"], row["session"]) == ("in_progress", CODE_SESSION)
        assert f"| S1 | Title S1 | #1131 | — | in_progress | — | {CODE_SESSION} |" in fake.bodies[EPIC]

    @pytest.mark.parametrize(
        ("row", "reason"),
        [
            pytest.param(
                _row("S1", child=1131, status="in_progress", session="bp_other"),
                "S1 is in_progress, not pending",
                id="taken",
            ),
            pytest.param(_row("S2", child=1132), "the work plan has no slice S1", id="unknown"),
        ],
    )
    def test_refuses_a_slice_that_is_not_pending(
        self, row: object, reason: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh({EPIC: _epic_body(row)})

        code = _main(monkeypatch, fake, "claim", "--issue", str(EPIC), "--slice", "S1", "--session", CODE_SESSION)

        assert code == 1
        assert capsys.readouterr().err == f"workplan.py: {reason}\n"
        assert fake.edits == []


class LandTests:
    def _argv(self, tmp_path: Path, plan: Mapping[str, object], *extra: str) -> tuple[str, ...]:
        """Return land's argv for S1 with PR 1215, a report, plan and an epic plan holding CONTRACT under tmp_path."""
        report = tmp_path / "slice-report.md"
        report.write_text("## Departures\n- Renamed a helper.\n", encoding="utf-8")
        plan_path = _write_json(tmp_path / "plan.json", plan)
        base_path = _write_json(tmp_path / "epic-plan.json", {"contract": CONTRACT, "slices": []})
        return (
            "land", "--issue", str(EPIC), "--slice", "S1", "--session", CODE_SESSION,
            "--pr", "1215", "--report", str(report), "--plan", plan_path, "--base-plan", base_path, *extra,
        )  # fmt: skip

    def _contract_body(self) -> str:
        """Return an epic body holding INTRO, CONTRACT_SECTION and a work plan whose S1 this code session claimed."""
        rows = [_row("S1", child=1131, status="in_progress", session=CODE_SESSION), _row("S2", child=1132)]
        return WORKPLAN.with_workplan(f"{INTRO}\n{CONTRACT_SECTION}", WORKPLAN.WorkPlan(PLAN_SESSION, 3, rows), "")

    def test_marks_in_review_and_appends_the_landed_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGh({EPIC: self._contract_body()})
        slice_question = {"id": "OQ-1", "summary": "Rename the helper? (assumed: no)", "assumed": "No."}
        plan = {
            "contract": CONTRACT,
            "slices": [_slice("S1", child=1131, phases=[1, 2]), _slice("S2", child=1132, phases=[])],
            "open_questions": [slice_question],
        }

        assert _main(monkeypatch, fake, *self._argv(tmp_path, plan)) == 0

        body = fake.bodies[EPIC]
        stored = _stored(body)
        assert (stored["plan_session"], stored["plan_revision"]) == (PLAN_SESSION, 3)
        assert (_rows(body)[0]["status"], _rows(body)[0]["pr"]) == ("in_review", 1215)
        assert body.startswith(f"{INTRO}\n{CONTRACT_SECTION}\n## Work plan\n")
        assert body.endswith("-->\n\n### S1 as landed (#1215)\n#### Departures\n- Renamed a helper.\n")

    def test_amended_contract_is_rerendered_keeping_the_epic_questions_and_repoints(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGh({EPIC: self._contract_body()})
        amended = {**CONTRACT, "conventions": [{"rule": "Use ok() everywhere", "entry": None, "new_in": "S1"}]}
        plan = {"contract": amended, "slices": [_slice("S1", child=1131), _slice("S2", child=1132)]}

        assert _main(monkeypatch, fake, *self._argv(tmp_path, plan)) == 0

        body = fake.bodies[EPIC]
        expected = CONTRACT_SECTION.replace("- Use ok (new;", "- Use ok() everywhere (new;")
        assert body.startswith(f"{INTRO}\n{expected}\n## Work plan\n")
        stored = _stored(body)
        assert (stored["plan_session"], stored["plan_revision"]) == (CODE_SESSION, None)

    def test_a_contract_already_amended_is_left_alone(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        amended = {**CONTRACT, "conventions": [{"rule": "Use ok() everywhere", "entry": None, "new_in": "S1"}]}
        body = self._contract_body().replace("- Use ok (new;", "- Use ok() everywhere (new;")
        fake = FakeGh({EPIC: body})
        plan = {"contract": amended, "slices": [_slice("S1", child=1131), _slice("S2", child=1132)]}

        assert _main(monkeypatch, fake, *self._argv(tmp_path, plan)) == 0

        assert fake.bodies[EPIC].split("## Work plan")[0] == body.split("## Work plan")[0]
        assert _stored(fake.bodies[EPIC])["plan_session"] == PLAN_SESSION

    def test_refuses_a_contract_edited_during_the_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh({EPIC: self._contract_body().replace("Output shapes disagree.", "Edited on GitHub.")})
        amended = {**CONTRACT, "conventions": [{"rule": "Use ok() everywhere", "entry": None, "new_in": "S1"}]}
        plan = {"contract": amended, "slices": [_slice("S1", child=1131), _slice("S2", child=1132)]}

        assert _main(monkeypatch, fake, *self._argv(tmp_path, plan)) == 1

        assert capsys.readouterr().err == (
            f"workplan.py: #{EPIC}'s Feature contract was edited during this run: it is neither the contract this "
            "run started from nor the amended one; reconcile it by hand and run land again\n"
        )
        assert fake.edits == []

    def test_a_decision_with_an_open_questions_id_settles_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGh({EPIC: self._contract_body()})
        settled = {"id": "OQ-1", "decision": "Drop v1", "why": "S1 landed without it", "settled_by": None}
        decisions = CONTRACT["decisions"]
        assert isinstance(decisions, list)
        decided = {**CONTRACT, "decisions": [*decisions, settled]}
        plan = {"contract": decided, "slices": [_slice("S1", child=1131), _slice("S2", child=1132)]}

        assert _main(monkeypatch, fake, *self._argv(tmp_path, plan)) == 0

        body = fake.bodies[EPIC]
        assert "| OQ-1 | Drop v1 | S1 landed without it |" in body
        assert "Open questions, each with a recommended answer" not in body
        assert _stored(body)["plan_session"] == CODE_SESSION

    def test_crlf_body_with_an_unchanged_contract_is_not_amended(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = self._contract_body().replace("\n", "\r\n")
        fake = FakeGh({EPIC: body})
        plan = {"contract": CONTRACT, "slices": [_slice("S1", child=1131), _slice("S2", child=1132)]}

        assert _main(monkeypatch, fake, *self._argv(tmp_path, plan)) == 0

        assert _stored(fake.bodies[EPIC])["plan_session"] == PLAN_SESSION
        assert WORKPLAN.lf(fake.bodies[EPIC]).startswith(f"{INTRO}\n{CONTRACT_SECTION}\n## Work plan\n")

    def test_amended_plan_repoints_the_plan_session(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeGh({EPIC: self._contract_body()})
        amended = [_slice("S1", child=1131), _slice("S2", child=1132, title="Narrower S2")]

        assert _main(monkeypatch, fake, *self._argv(tmp_path, {"contract": CONTRACT, "slices": amended})) == 0

        stored = _stored(fake.bodies[EPIC])
        assert (stored["plan_session"], stored["plan_revision"]) == (CODE_SESSION, None)
        assert _rows(fake.bodies[EPIC])[1]["title"] == "Narrower S2"
        assert f"Plan Session ID: `{CODE_SESSION}`\n" in fake.bodies[EPIC]

    def test_applies_sweep_entries_and_skips_a_changed_body(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh(
            {
                EPIC: _epic_body(_row("S1", child=1131, status="in_progress", session=CODE_SESSION)),
                1060: "Old 1060.\n",
                1061: "Old 1061.\n",
            }
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
        fake.bodies[1061] = "Edited since the sweep was drafted.\n"
        sweep = _write_json(tmp_path / "sweep.json", entries)
        plan = {"contract": None, "slices": [_slice("S1", child=1131)]}

        assert _main(monkeypatch, fake, *self._argv(tmp_path, plan, "--sweep", sweep)) == 0

        assert capsys.readouterr().err == (
            "workplan.py: sweep skipped #1061: its body changed since the sweep was drafted\n"
        )
        assert (fake.bodies[1060], fake.bodies[1061]) == ("New 1060.\n", "Edited since the sweep was drafted.\n")
        assert fake.comments == [(1060, "Swept 1060.\n")]
        assert fake.removed_labels == [(1060, "dovo:ready")]

    def test_accepts_an_empty_sweep(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeGh({EPIC: _epic_body(_row("S1", child=1131, status="in_progress", session=CODE_SESSION))})
        sweep = _write_json(tmp_path / "sweep.json", [])
        plan = {"contract": None, "slices": [_slice("S1", child=1131)]}

        assert _main(monkeypatch, fake, *self._argv(tmp_path, plan, "--sweep", sweep)) == 0

        assert (fake.edits, fake.comments) == ([EPIC], [])

    def test_refuses_a_slice_this_session_did_not_claim(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeGh({EPIC: _epic_body(_row("S1", child=1131))})
        plan = {"contract": None, "slices": [_slice("S1", child=1131)]}

        assert _main(monkeypatch, fake, *self._argv(tmp_path, plan)) == 1

        assert capsys.readouterr().err == (
            f"workplan.py: S1 is pending for session None, not in_progress for {CODE_SESSION}\n"
        )
        assert fake.edits == []


@pytest.mark.parametrize(
    ("outputs", "text"),
    [
        pytest.param({"slice": "S1"}, "slice=S1\n", id="single-line"),
        pytest.param({"a": "x\ny"}, "a<<WORKPLAN_EOF\nx\ny\nWORKPLAN_EOF\n", id="multi-line"),
    ],
)
def test_output_lines(outputs: dict[str, str], text: str) -> None:
    assert WORKPLAN.output_lines(outputs) == text
