"""Tier 1 contract: scripts/reply_threads.py replies on the review threads a pr-fix triage addressed, resolves the fixed and dismissed ones and posts one outcome comment for out-of-diff findings."""

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

PR_URL = "https://github.com/o/r/pull/7"
COMMIT = "9f3c2a1"
REVIEWED = "1a2b3c4" + "d" * 33
TRIAGE = ".agentic/triage.json"
OUTCOMES = ".agentic/feedback-outcomes.json"
MARKER = "<!-- dovo-pr-fix -->"


def _load_script(name: str, module_name: str) -> ModuleType:
    """Load scripts/<name>.py by path as module_name, since scripts/ is not a package."""
    spec = importlib.util.spec_from_file_location(module_name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


PUBLISH = _load_script("publish_review", "publish_review")
FEEDBACK = _load_script("pr_feedback", "pr_feedback")
REPLY = _load_script("reply_threads", "reply_threads_under_test")

W2: dict[str, object] = {
    "id": "W2",
    "severity": "WARNING",
    "path": "scripts/publish_review.py",
    "start_line": 120,
    "line": 124,
    "rule": "R17",
    "summary": "The docstring names a round.",
    "issue": "The docstring names a round.",
    "fix": "Drop the round.",
}


def _item(item_id: str, source: str, thread: str | None, url: str, body: str) -> dict[str, object]:
    """Return a feedback.json item with its label."""
    return {
        "id": item_id,
        "source": source,
        "thread": thread,
        "url": url,
        "body": body,
        "label": f"{item_id} [{source}]",
    }


FEEDBACK_ITEMS: list[dict[str, object]] = [
    _item("T1", "review_thread", "PRRT_1", "https://x/t1", "Hold the lock"),
    _item("T2", "review_thread", "PRRT_2", "https://x/t2", "Rename it"),
    _item("T3", "review_thread", "PRRT_3", "https://x/t3", "Split it"),
    _item("T4", "review_thread", "PRRT_4", "https://x/t4", "Cache it"),
    {
        **_item("R1", "review_finding", None, "https://x/review", PUBLISH.finding_block(W2)),
        "finding": {"id": "W2", "severity": "WARNING", "rule": "R17"},
        "commit": REVIEWED,
    },
    _item("C1", "comment", None, "https://x/c1", "Walkthrough"),
    _item("K1", "check", None, "https://ci/tests", "Tests: failure"),
]
TRIAGE_ITEMS: list[dict[str, object]] = [
    {"id": "T1", "action": "fix", "reason": "The lock is not held."},
    {"id": "T2", "action": "dismiss", "reply": "The name is the\nproject's term."},
    {"id": "T3", "action": "defer", "reply": "#1300 splits it."},
    {"id": "T4", "action": "fix", "reason": "The value is recomputed."},
    {"id": "R1", "action": "dismiss", "reply": "The issue body is edited by a person before merge."},
    {"id": "C1", "action": "fix", "reason": "The walkthrough is stale."},
    {"id": "K1", "action": "defer", "reply": "The runner is down."},
]
OUTCOME_ITEMS: list[dict[str, object]] = [
    {"id": "T1", "outcome": "fixed", "note": "Holds the lock."},
    {"id": "T4", "outcome": "deferred", "note": "The cache needs a design."},
    {"id": "C1", "outcome": "fixed", "note": "Refreshed it."},
]
REPLIES = {
    "PRRT_1": f"{MARKER}\nFixed in {COMMIT}: Holds the lock.",
    "PRRT_2": f"{MARKER}\nNot changed: The name is the project's term.",
    "PRRT_3": f"{MARKER}\nDeferred: #1300 splits it.",
    "PRRT_4": f"{MARKER}\nDeferred: The cache needs a design.",
}
OUTCOME_COMMENT = (
    "<!-- dovo-pr-fix-outcomes -->\n"
    "`W2` · `scripts/publish_review.py:120-124` · 1a2b3c4: Not changed: The issue body is edited by a person before merge."
)


def _without(entries: list[dict[str, object]], item_id: str) -> list[dict[str, object]]:
    """Return entries without the one for item_id."""
    return [entry for entry in entries if entry["id"] != item_id]


def _replace(entries: list[dict[str, object]], item_id: str, **changes: object) -> list[dict[str, object]]:
    """Return entries with item_id's entry updated by changes."""
    return [{**entry, **changes} if entry["id"] == item_id else entry for entry in entries]


def _outcome(item_id: str, outcome: str, note: str = "A note.") -> dict[str, object]:
    """Return an outcomes entry for item_id."""
    return {"id": item_id, "outcome": outcome, "note": note}


def _thread(*, resolved: bool = False, comments: list[dict[str, object]] | None = None) -> dict[str, object]:
    """Return the REPLIES_QUERY `.data.node` answer for a thread."""
    return {"isResolved": resolved, "comments": {"nodes": comments or [{"author": {"login": "bob"}, "body": "x"}]}}


def _write_agentic(
    root: Path,
    *,
    triage: object | None,
    outcomes: object | None,
    items: list[dict[str, object]] | None = None,
) -> None:
    """Write .agentic/feedback.json with items (FEEDBACK_ITEMS by default), pr-url and, unless None, triage and outcomes."""
    agentic = root / ".agentic"
    agentic.mkdir()
    feedback = {"pr": 7, "head": "a" * 40, "items": FEEDBACK_ITEMS if items is None else items}
    (agentic / "feedback.json").write_text(json.dumps(feedback), encoding="utf-8")
    (agentic / "pr-url").write_text(f"{PR_URL}\n", encoding="utf-8")
    if triage is not None:
        (agentic / "triage.json").write_text(json.dumps(triage), encoding="utf-8")
    if outcomes is not None:
        (agentic / "feedback-outcomes.json").write_text(json.dumps(outcomes), encoding="utf-8")


class ReplyThreadsTests:
    @pytest.mark.parametrize(
        ("outcome", "second_line"),
        [
            pytest.param("fixed", f"Fixed in {COMMIT}: Hold the lock. Around the write.", id="fixed"),
            pytest.param("dismissed", "Not changed: Hold the lock. Around the write.", id="dismissed"),
            pytest.param("deferred", "Deferred: Hold the lock. Around the write.", id="deferred"),
        ],
    )
    def test_reply_body(self, outcome: str, second_line: str) -> None:
        body = REPLY.reply_body(REPLY.Outcome(outcome, "Hold the lock.\n\n  Around\tthe write. "), COMMIT)

        assert body == f"{MARKER}\n{second_line}"

    @pytest.mark.parametrize(
        ("triage", "item_ids", "expected"),
        [
            pytest.param({"items": TRIAGE_ITEMS[:3]}, ["T1", "T2", "T3"], None, id="valid"),
            pytest.param([], ["T1", "T2", "T3"], f"{TRIAGE} has no entry for T1, T2, T3", id="not-object"),
            pytest.param({}, [], f"{TRIAGE} has no items list", id="no-items-list"),
            pytest.param(
                {"items": TRIAGE_ITEMS[:2]}, ["T1", "T2", "T3"], f"{TRIAGE} has no entry for T3", id="missing-entry"
            ),
            pytest.param(
                {"items": [*TRIAGE_ITEMS[:3], {"id": "X9", "action": "fix", "reason": "r"}]},
                ["T1", "T2", "T3"],
                f"{TRIAGE} entry X9 names no feedback item",
                id="unknown-entry",
            ),
            pytest.param(
                {"items": [*TRIAGE_ITEMS[:3], TRIAGE_ITEMS[0]]},
                ["T1", "T2", "T3"],
                f"{TRIAGE} has more than one entry for T1",
                id="repeated-entry",
            ),
            pytest.param(
                {"items": _replace(TRIAGE_ITEMS[:3], "T2", action="skip")},
                ["T1", "T2", "T3"],
                f"{TRIAGE} entry T2 has action 'skip', not fix, dismiss or defer",
                id="bad-action",
            ),
            pytest.param(
                {"items": _replace(TRIAGE_ITEMS[:3], "T2", reply=None)},
                ["T1", "T2", "T3"],
                f"{TRIAGE} entry T2 has no reply",
                id="dismiss-no-reply",
            ),
            pytest.param(
                {"items": _replace(TRIAGE_ITEMS[:3], "T3", reply=" ")},
                ["T1", "T2", "T3"],
                f"{TRIAGE} entry T3 has no reply",
                id="defer-blank-reply",
            ),
            pytest.param(
                {"items": _replace(TRIAGE_ITEMS[:3], "T1", reason="")},
                ["T1", "T2", "T3"],
                f"{TRIAGE} entry T1 has no reason",
                id="fix-no-reason",
            ),
        ],
    )
    def test_triage_problem(self, triage: object, item_ids: list[str], expected: str | None) -> None:
        assert REPLY.triage_problem(triage, item_ids) == expected

    @pytest.mark.parametrize(
        ("items", "triage", "expected"),
        [
            pytest.param(
                FEEDBACK_ITEMS[:3],
                {"items": list(reversed(TRIAGE_ITEMS[:3]))},
                "T1 [review_thread] → fix: The lock is not held.\n"
                "T2 [review_thread] → dismiss: The name is the project's term.\n"
                "T3 [review_thread] → defer: #1300 splits it.\n",
                id="feedback-order",
            ),
            pytest.param([], {"items": []}, "No open feedback.\n", id="no-items"),
        ],
    )
    def test_triage_markdown(self, items: list[dict[str, object]], triage: dict[str, object], expected: str) -> None:
        assert REPLY.triage_markdown(items, triage) == expected

    def test_main_renders_triage(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ran: list[tuple[str, ...]] = []
        _setup(tmp_path, monkeypatch, _fake_run(ran, []), triage={"items": TRIAGE_ITEMS}, render=True)

        assert REPLY.main() == 0

        assert capsys.readouterr().out == "Rendered 7 triage entries to .agentic/triage.md\n"
        assert (tmp_path / ".agentic" / "triage.md").read_text(encoding="utf-8") == (
            "T1 [review_thread] → fix: The lock is not held.\n"
            "T2 [review_thread] → dismiss: The name is the project's term.\n"
            "T3 [review_thread] → defer: #1300 splits it.\n"
            "T4 [review_thread] → fix: The value is recomputed.\n"
            "R1 [review_finding] → dismiss: The issue body is edited by a person before merge.\n"
            "C1 [comment] → fix: The walkthrough is stale.\n"
            "K1 [check] → defer: The runner is down.\n"
        )
        assert ran == []

    @pytest.mark.parametrize(
        ("triage", "expected"),
        [
            pytest.param(None, f"{TRIAGE} is missing or not JSON", id="triage-absent"),
            pytest.param({"items": _without(TRIAGE_ITEMS, "T2")}, f"{TRIAGE} has no entry for T2", id="missing-entry"),
            pytest.param(
                {"items": _replace(TRIAGE_ITEMS, "C1", reason=None)},
                f"{TRIAGE} entry C1 has no reason",
                id="fix-no-reason",
            ),
        ],
    )
    def test_main_render_triage_refuses(
        self,
        triage: object | None,
        expected: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        ran: list[tuple[str, ...]] = []
        _setup(tmp_path, monkeypatch, _fake_run(ran, []), items=FEEDBACK_ITEMS, triage=triage, render=True)

        assert REPLY.main() == 1

        assert capsys.readouterr().err == f"reply_threads.py: {expected}\n"
        assert not (tmp_path / ".agentic" / "triage.md").exists()
        assert ran == []

    @pytest.mark.parametrize(
        ("outcomes", "expected"),
        [
            pytest.param(
                {"items": [_outcome("T1", "fixed"), _outcome("K1", "deferred"), _outcome("X9", "dismissed")]},
                None,
                id="valid",
            ),
            pytest.param(
                {"round": 2, "items": [_outcome("T1", "fixed")]}, f"{OUTCOMES} has a round key", id="round-key"
            ),
            pytest.param({"items": [_outcome("T1", "fixed")]}, f"{OUTCOMES} has no outcome for K1", id="missing-id"),
            pytest.param(
                {"items": [_outcome("T1", "dismissed"), _outcome("K1", "fixed")]},
                f"{OUTCOMES} entry T1 has outcome 'dismissed', not fixed or deferred",
                id="dismissed-outcome",
            ),
            pytest.param(
                {"items": [_outcome("T1", "done"), _outcome("K1", "fixed")]},
                f"{OUTCOMES} entry T1 has outcome 'done', not fixed or deferred",
                id="unknown-outcome",
            ),
            pytest.param(
                {"items": [_outcome("T1", "fixed"), _outcome("K1", "fixed", " ")]},
                f"{OUTCOMES} entry K1 has no note",
                id="empty-note",
            ),
        ],
    )
    def test_outcomes_problem(self, outcomes: object, expected: str | None) -> None:
        assert REPLY.outcomes_problem(outcomes, ["T1", "K1"]) == expected

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            pytest.param("#### W1 · `a.py:3` · R1\n\nsee `b.py`", "a.py:3", id="path-line"),
            pytest.param("#### W1 · `a.py:3-5` · R1", "a.py:3-5", id="path-range"),
            pytest.param("#### W1 · `a.py`", "a.py", id="path-only"),
            pytest.param("#### W1 · R1\n\n**Issue:** `b.py` breaks.", None, id="no-location"),
        ],
    )
    def test_heading_location(self, body: str, expected: str | None) -> None:
        assert REPLY.heading_location(body) == expected

    def test_location_of_posted_entry(self) -> None:
        items = FEEDBACK.finding_items({"body": PUBLISH.review_body([W2]), "url": "https://x/review"})

        assert REPLY.heading_location(str(items[0]["body"])) == "scripts/publish_review.py:120-124"

    def test_outcome_comment(self) -> None:
        findings = [
            REPLY.FindingOutcome(
                "W2",
                "scripts/publish_review.py:120-124",
                REPLY.Outcome("dismissed", "The issue body is edited by a person before merge."),
            ),
            REPLY.FindingOutcome(
                "S1",
                "src/dovo/common/interrupt.py:44",
                REPLY.Outcome("fixed", "install_interrupt_handlers skips an inherited SIG_IGN."),
            ),
            REPLY.FindingOutcome("N1", None, REPLY.Outcome("deferred", "The docs slice\nrewrites it.")),
        ]

        assert REPLY.outcome_comment(findings, "1a2b3c4", "5d6e7f8") == (
            "<!-- dovo-pr-fix-outcomes -->\n"
            "`W2` · `scripts/publish_review.py:120-124` · 1a2b3c4: Not changed: The issue body is edited by a person before merge.\n"
            "`S1` · `src/dovo/common/interrupt.py:44` · 1a2b3c4: Fixed in 5d6e7f8: install_interrupt_handlers skips an inherited SIG_IGN.\n"
            "`N1` · 1a2b3c4: Deferred: The docs slice rewrites it."
        )

    def test_main_replies_resolves_and_posts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        posted: list[tuple[str, ...]] = []
        _setup(tmp_path, monkeypatch, _fake_run([], posted))

        assert REPLY.main() == 0

        assert capsys.readouterr().out == (
            f"Replied on 4 review threads, resolved 2 and posted 1 outcome comment for {PR_URL}\n"
        )
        assert posted == [
            ("reply", "PRRT_1", REPLIES["PRRT_1"]),
            ("resolve", "PRRT_1"),
            ("reply", "PRRT_2", REPLIES["PRRT_2"]),
            ("resolve", "PRRT_2"),
            ("reply", "PRRT_3", REPLIES["PRRT_3"]),
            ("reply", "PRRT_4", REPLIES["PRRT_4"]),
            ("comment", OUTCOME_COMMENT),
        ]

    @pytest.mark.parametrize(
        ("earlier", "expected_posted", "count"),
        [
            pytest.param(OUTCOME_COMMENT, [], 0, id="identical-comment"),
            pytest.param(
                "<!-- dovo-pr-fix-outcomes -->\n`W1` · 0000000: Deferred: Later.",
                [("comment", OUTCOME_COMMENT)],
                1,
                id="other-comment",
            ),
        ],
    )
    def test_main_retry_posts_nothing(
        self,
        earlier: str,
        expected_posted: list[tuple[str, ...]],
        count: int,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        posted: list[tuple[str, ...]] = []
        threads = {
            thread: _thread(
                resolved=thread in ("PRRT_1", "PRRT_2"), comments=[{"author": {"login": "me"}, "body": body}]
            )
            for thread, body in REPLIES.items()
        }
        comments: list[dict[str, object]] = [{"id": 12, "user": "me", "body": earlier}]
        _setup(tmp_path, monkeypatch, _fake_run([], posted, threads, comments=comments))

        assert REPLY.main() == 0

        assert capsys.readouterr().out == (
            f"Replied on 0 review threads, resolved 0 and posted {count} outcome comment for {PR_URL}\n"
        )
        assert posted == expected_posted

    @pytest.mark.parametrize(
        ("items", "triage", "outcomes"),
        [
            pytest.param([], {"items": []}, None, id="no-items"),
            pytest.param(
                FEEDBACK_ITEMS[5:],
                {"items": TRIAGE_ITEMS[5:]},
                {"items": [OUTCOME_ITEMS[2]]},
                id="only-c-and-k",
            ),
        ],
    )
    def test_main_posts_nothing_without_threads_or_findings(
        self,
        items: list[dict[str, object]],
        triage: dict[str, object],
        outcomes: dict[str, object] | None,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        ran: list[tuple[str, ...]] = []
        _setup(tmp_path, monkeypatch, _fake_run(ran, []), items=items, triage=triage, outcomes=outcomes)

        assert REPLY.main() == 0

        assert capsys.readouterr().out == (
            f"Replied on 0 review threads, resolved 0 and posted 0 outcome comment for {PR_URL}\n"
        )
        assert ran == []

    @pytest.mark.parametrize(
        ("items", "triage", "outcomes", "expected"),
        [
            pytest.param(None, None, {"items": OUTCOME_ITEMS}, f"{TRIAGE} is missing or not JSON", id="triage-absent"),
            pytest.param(
                None,
                {"items": _without(TRIAGE_ITEMS, "T2")},
                {"items": OUTCOME_ITEMS},
                f"{TRIAGE} has no entry for T2",
                id="missing-entry",
            ),
            pytest.param(
                None,
                {"items": TRIAGE_ITEMS},
                None,
                f"{OUTCOMES} is missing or not JSON; no outcome for T1, T4, C1",
                id="outcomes-absent",
            ),
            pytest.param(
                None,
                {"items": TRIAGE_ITEMS},
                {"round": 2, "items": OUTCOME_ITEMS},
                f"{OUTCOMES} has a round key",
                id="round-key",
            ),
            pytest.param(
                _replace(FEEDBACK_ITEMS, "T3", thread=None),
                {"items": TRIAGE_ITEMS},
                {"items": OUTCOME_ITEMS},
                ".agentic/feedback.json item T3 names no review thread",
                id="thread-without-id",
            ),
            pytest.param(
                _replace(FEEDBACK_ITEMS, "R1", finding=None),
                {"items": TRIAGE_ITEMS},
                {"items": OUTCOME_ITEMS},
                ".agentic/feedback.json item R1 names no dovo finding",
                id="r-no-finding",
            ),
            pytest.param(
                [{key: value for key, value in item.items() if key != "commit"} for item in FEEDBACK_ITEMS],
                {"items": TRIAGE_ITEMS},
                {"items": OUTCOME_ITEMS},
                ".agentic/feedback.json item R1 names no reviewed commit",
                id="r-no-commit",
            ),
        ],
    )
    def test_main_stops_before_posting(
        self,
        items: list[dict[str, object]] | None,
        triage: object | None,
        outcomes: object | None,
        expected: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        ran: list[tuple[str, ...]] = []
        _setup(tmp_path, monkeypatch, _fake_run(ran, []), items=items, triage=triage, outcomes=outcomes)

        assert REPLY.main() == 1

        assert capsys.readouterr().err == f"reply_threads.py: {expected}\n"
        assert ran == []

    def test_main_stops_before_writing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        posted: list[tuple[str, ...]] = []
        triage = {"items": _replace(TRIAGE_ITEMS, "R1", reply="x" * 65_536)}
        _setup(tmp_path, monkeypatch, _fake_run([], posted), triage=triage, outcomes={"items": OUTCOME_ITEMS})

        assert REPLY.main() == 1

        length = len(OUTCOME_COMMENT) - len(str(TRIAGE_ITEMS[4]["reply"])) + 65_536
        expected = f"the outcome comment is {length} characters; GitHub accepts at most 65536"
        assert capsys.readouterr().err == f"reply_threads.py: {expected}\n"
        assert posted == []

    @pytest.mark.parametrize(
        ("failing", "expected", "kept"),
        [
            pytest.param(("reply", "PRRT_2"), "cannot update review thread https://x/t2: HTTP 502", 2, id="reply"),
            pytest.param(("resolve", "PRRT_2"), "cannot update review thread https://x/t2: HTTP 502", 2, id="resolve"),
            pytest.param(
                ("replies_query", "PRRT_2"), "cannot update review thread https://x/t2: HTTP 502", 2, id="replies_query"
            ),
            pytest.param(("comment", ""), f"cannot post the outcome comment on {PR_URL}: HTTP 502", 6, id="comment"),
        ],
    )
    def test_main_reports_failed_post(
        self,
        failing: tuple[str, str],
        expected: str,
        kept: int,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        posted: list[tuple[str, ...]] = []
        _setup(tmp_path, monkeypatch, _fake_run([], posted, failing=failing))

        assert REPLY.main() == 1

        assert capsys.readouterr().err == f"reply_threads.py: {expected}\n"
        expected_posted = [
            ("reply", "PRRT_1", REPLIES["PRRT_1"]),
            ("resolve", "PRRT_1"),
            ("reply", "PRRT_2", REPLIES["PRRT_2"]),
            ("resolve", "PRRT_2"),
            ("reply", "PRRT_3", REPLIES["PRRT_3"]),
            ("reply", "PRRT_4", REPLIES["PRRT_4"]),
        ]
        assert posted[:kept] == expected_posted[:kept]


def _setup(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_run: Callable[..., str],
    *,
    items: list[dict[str, object]] | None = None,
    triage: object | None = None,
    outcomes: object | None = None,
    render: bool = False,
) -> None:
    """Write the .agentic/ inputs under root and run from it, answering commands through fake_run.

    triage and outcomes default to TRIAGE_ITEMS and OUTCOME_ITEMS when items is also left out;
    render adds --render-triage.
    """
    defaults = items is None and triage is None and outcomes is None
    monkeypatch.chdir(root)
    _write_agentic(
        root,
        items=items,
        triage={"items": TRIAGE_ITEMS} if defaults else triage,
        outcomes={"items": OUTCOME_ITEMS} if defaults else outcomes,
    )
    monkeypatch.setattr(sys, "argv", ["reply_threads.py", *(["--render-triage"] if render else [])])
    monkeypatch.setattr(REPLY, "run", fake_run)
    monkeypatch.setattr(PUBLISH, "run", fake_run)


def _fake_run(
    ran: list[tuple[str, ...]],
    posted: list[tuple[str, ...]],
    threads: dict[str, dict[str, object]] | None = None,
    *,
    comments: list[dict[str, object]] | None = None,
    failing: tuple[str, str] | None = None,
) -> Callable[..., str]:
    """Return a `run` double answering one reply step on PR #7, recording each command in ran.

    `git rev-parse --short HEAD` answers COMMIT and the viewer lookup `me`. The comment listing answers
    comments, or none. The replies query answers the thread's node from threads, or an
    unresolved thread with someone else's comment. Each reply mutation is recorded in posted as
    ("reply", thread, body), each resolve as ("resolve", thread) and each comment written as ("comment", body),
    read from its `body=@<file>` field. failing names a (call, thread) pair, call being `reply`, `resolve`,
    `replies_query` or `comment` (with thread ""), that raises CalledProcessError.
    """
    listed = [] if comments is None else comments

    def fake_run(*args: str) -> str:
        ran.append(args)
        if args[0] == "git":
            assert args == ("git", "rev-parse", "--short", "HEAD"), f"ran {args}"
            return COMMIT
        if args[-3:] == ("user", "--jq", ".login"):
            return "me"
        if args[2] != "graphql":
            return _rest_call(args, listed, posted, failing)
        return _graphql_answer(args, threads or {}, posted, failing)

    return fake_run


def _graphql_answer(
    args: tuple[str, ...],
    threads: dict[str, dict[str, object]],
    posted: list[tuple[str, ...]],
    failing: tuple[str, str] | None,
) -> str:
    """Answer the replies query from threads, or record a reply or resolve in posted, failing it when failing names it."""
    call, thread, fields = _graphql_call(args)
    if failing == (call, thread):
        raise subprocess.CalledProcessError(1, list(args), stderr="HTTP 502\n")
    if call == "replies_query":
        return json.dumps(threads.get(thread, _thread()))
    posted.append((call, thread, *fields))
    return ""


def _rest_call(
    args: tuple[str, ...],
    listed: list[dict[str, object]],
    posted: list[tuple[str, ...]],
    failing: tuple[str, str] | None,
) -> str:
    """Answer the comment listing with listed, or record a comment write in posted, failing it when failing names it."""
    assert args[:4] == ("gh", "api", "--hostname", "github.com"), f"ran {args}"
    if "--paginate" in args:
        assert "repos/o/r/issues/7/comments" in args, f"ran {args}"
        return "\n".join(json.dumps(comment) for comment in listed)
    assert args[4] == "repos/o/r/issues/7/comments", f"ran {args}"
    if failing == ("comment", ""):
        raise subprocess.CalledProcessError(1, list(args), stderr="HTTP 502\n")
    body_file = Path(args[args.index("-F") + 1].removeprefix("body=@"))
    posted.append(("comment", body_file.read_text(encoding="utf-8")))
    return "https://github.com/o/r/pull/7#issuecomment-1"


def _graphql_call(args: tuple[str, ...]) -> tuple[str, str, list[str]]:
    """Return which GraphQL call args runs, its thread id and the bodies of any further `-f` fields."""
    assert args[:5] == ("gh", "api", "graphql", "--hostname", "github.com"), f"ran {args}"
    calls = {
        f"query={REPLY.REPLIES_QUERY}": "replies_query",
        f"query={PUBLISH.REPLY_MUTATION}": "reply",
        f"query={PUBLISH.RESOLVE_MUTATION}": "resolve",
    }
    fields = [args[index + 1] for index, arg in enumerate(args) if arg == "-f"]
    thread = fields[1].removeprefix("thread=")
    return calls[fields[0]], thread, [field.removeprefix("body=") for field in fields[2:]]
