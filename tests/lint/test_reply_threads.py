"""Tier 1 contract: scripts/reply_threads.py replies on the review threads pr-fix addressed and resolves the fixed ones."""

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
OUTCOMES = ".agentic/feedback-outcomes.json"
MARKER = "<!-- dovo-pr-fix round 2 -->"


def _load_script(name: str, module_name: str) -> ModuleType:
    """Load scripts/<name>.py by path as module_name, since scripts/ is not a package."""
    spec = importlib.util.spec_from_file_location(module_name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_load_script("publish_review", "publish_review")
_load_script("pr_feedback", "pr_feedback")
REPLY = _load_script("reply_threads", "reply_threads_under_test")

FEEDBACK_ITEMS: list[dict[str, object]] = [
    {"id": "T1", "source": "review_thread", "thread": "PRRT_1", "url": "https://x/t1", "body": "Hold the lock"},
    {"id": "T2", "source": "review_thread", "thread": "PRRT_2", "url": "https://x/t2", "body": "Rename it"},
    {"id": "C1", "source": "comment", "thread": None, "url": "https://x/c1", "body": "Walkthrough"},
    {"id": "K1", "source": "check", "thread": None, "url": "https://ci/tests", "body": "Tests: failure"},
]


def _outcome(item_id: str, outcome: str, note: str = "A note.") -> dict[str, object]:
    """Return an outcomes entry for item_id."""
    return {"id": item_id, "outcome": outcome, "note": note}


def _thread(*, resolved: bool = False, comments: list[dict[str, object]] | None = None) -> dict[str, object]:
    """Return the REPLIES_QUERY `.data.node` answer for a thread."""
    return {"isResolved": resolved, "comments": {"nodes": comments or [{"author": {"login": "bob"}, "body": "x"}]}}


def _write_agentic(root: Path, outcomes: dict[str, object] | None) -> None:
    """Write .agentic/feedback.json with FEEDBACK_ITEMS, .agentic/pr-url and, unless None, the outcomes file."""
    agentic = root / ".agentic"
    agentic.mkdir()
    feedback = {"pr": 7, "head": "a" * 40, "session": "s", "items": FEEDBACK_ITEMS}
    (agentic / "feedback.json").write_text(json.dumps(feedback), encoding="utf-8")
    (agentic / "pr-url").write_text(f"{PR_URL}\n", encoding="utf-8")
    if outcomes is not None:
        (agentic / "feedback-outcomes.json").write_text(json.dumps(outcomes), encoding="utf-8")


class ReplyThreadsTests:
    @pytest.mark.parametrize(
        ("outcome", "second_line"),
        [
            pytest.param("fixed", f"Fixed in {COMMIT}: Hold the lock. Around the write.", id="fixed"),
            pytest.param("disputed", "Not changed: Hold the lock. Around the write.", id="disputed"),
            pytest.param("deferred", "Deferred: Hold the lock. Around the write.", id="deferred"),
        ],
    )
    def test_reply_body(self, outcome: str, second_line: str) -> None:
        body = REPLY.reply_body(2, outcome, "Hold the lock.\n\n  Around\tthe write. ", COMMIT)

        assert body == f"{MARKER}\n{second_line}"

    @pytest.mark.parametrize(
        ("outcomes", "expected"),
        [
            pytest.param(
                {"round": 2, "items": [_outcome("T1", "fixed"), _outcome("K1", "deferred"), _outcome("X9", "bad")]},
                None,
                id="valid",
            ),
            pytest.param(
                {"round": 2, "items": [_outcome("T1", "fixed")]}, f"{OUTCOMES} has no outcome for K1", id="missing-id"
            ),
            pytest.param(
                {"round": 2, "items": [_outcome("T1", "done"), _outcome("K1", "fixed")]},
                f"{OUTCOMES} entry T1 has outcome 'done', not fixed, disputed or deferred",
                id="unknown-outcome",
            ),
            pytest.param(
                {"round": 2, "items": [_outcome("T1", "fixed"), _outcome("K1", "fixed", " ")]},
                f"{OUTCOMES} entry K1 has no note",
                id="empty-note",
            ),
            pytest.param({"round": 0, "items": []}, f"{OUTCOMES} round 0 is not a positive integer", id="round-0"),
            pytest.param(
                {"round": "2", "items": []}, f"{OUTCOMES} round '2' is not a positive integer", id="round-str"
            ),
            pytest.param(
                {"round": True, "items": []}, f"{OUTCOMES} round True is not a positive integer", id="round-true"
            ),
        ],
    )
    def test_outcomes_problem(self, outcomes: object, expected: str | None) -> None:
        assert REPLY.outcomes_problem(outcomes, ["T1", "K1"]) == expected

    @pytest.mark.parametrize(
        ("comments", "expected"),
        [
            pytest.param([{"author": {"login": "me"}, "body": f"{MARKER}\nFixed in x: y"}], True, id="own-same-round"),
            pytest.param([{"author": {"login": "bob"}, "body": f"{MARKER}\nFixed in x: y"}], False, id="other-author"),
            pytest.param(
                [{"author": {"login": "me"}, "body": "<!-- dovo-pr-fix round 1 -->\nFixed in x: y"}],
                False,
                id="own-other-round",
            ),
            pytest.param(
                [{"author": {"login": "me"}, "body": f"Quoting:\n{MARKER}"}], False, id="marker-not-first-line"
            ),
        ],
    )
    def test_already_replied(self, comments: list[dict[str, object]], expected: bool) -> None:
        assert REPLY.already_replied([{"author": None, "body": "x"}, *comments], "me", 2) is expected

    def test_main_replies_and_resolves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ran: list[tuple[str, ...]] = []
        posted: list[tuple[str, ...]] = []
        outcomes: dict[str, object] = {
            "round": 2,
            "items": [_outcome("T1", "fixed", "Holds the lock."), _outcome("T2", "disputed")],
        }
        _setup(tmp_path, monkeypatch, outcomes, '["T1", "T2"]', _fake_run(ran, posted, {}))

        assert REPLY.main() == 0

        assert capsys.readouterr().out == f"Replied on 2 review threads and resolved 1 for {PR_URL}\n"
        assert posted == [
            ("reply", "PRRT_1", f"{MARKER}\nFixed in {COMMIT}: Holds the lock."),
            ("resolve", "PRRT_1"),
            ("reply", "PRRT_2", f"{MARKER}\nNot changed: A note."),
        ]

    def test_main_resolves_without_replying_twice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ran: list[tuple[str, ...]] = []
        posted: list[tuple[str, ...]] = []
        own: dict[str, object] = {"author": {"login": "me"}, "body": f"{MARKER}\nFixed in {COMMIT}: A note."}
        threads = {"PRRT_1": _thread(comments=[own])}
        outcomes: dict[str, object] = {"round": 2, "items": [_outcome("T1", "fixed")]}
        _setup(tmp_path, monkeypatch, outcomes, '["T1"]', _fake_run(ran, posted, threads))

        assert REPLY.main() == 0

        assert capsys.readouterr().out == f"Replied on 0 review threads and resolved 1 for {PR_URL}\n"
        assert posted == [("resolve", "PRRT_1")]

    @pytest.mark.parametrize(
        ("outcomes", "expected"),
        [
            pytest.param(None, f"{OUTCOMES} is missing or not JSON; no outcome for T1, K1", id="file-absent"),
            pytest.param(
                {"round": 2, "items": [_outcome("T1", "fixed")]}, f"{OUTCOMES} has no outcome for K1", id="k1-missing"
            ),
        ],
    )
    def test_main_stops_before_posting(
        self,
        outcomes: dict[str, object] | None,
        expected: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        ran: list[tuple[str, ...]] = []
        _setup(tmp_path, monkeypatch, outcomes, '["T1", "K1"]', _fake_run(ran, [], {}))

        assert REPLY.main() == 1

        assert capsys.readouterr().err == f"reply_threads.py: {expected}\n"
        assert ran == []

    @pytest.mark.parametrize("failing_call", ["reply", "resolve", "replies_query"])
    def test_main_reports_failed_reply(
        self, failing_call: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        posted: list[tuple[str, ...]] = []
        outcomes: dict[str, object] = {"round": 2, "items": [_outcome("T1", "fixed"), _outcome("T2", "fixed")]}
        fake_run = _fake_run([], posted, {}, failing=(failing_call, "PRRT_2"))
        _setup(tmp_path, monkeypatch, outcomes, '["T1", "T2"]', fake_run)

        assert REPLY.main() == 1

        assert capsys.readouterr().err == "reply_threads.py: cannot update review thread https://x/t2: HTTP 502\n"
        assert posted[:2] == [("reply", "PRRT_1", f"{MARKER}\nFixed in {COMMIT}: A note."), ("resolve", "PRRT_1")]

    @pytest.mark.parametrize("selected", ["[]", '["C1", "K1"]'])
    def test_main_posts_nothing_without_threads(
        self, selected: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ran: list[tuple[str, ...]] = []
        monkeypatch.chdir(tmp_path)
        if selected != "[]":
            _write_agentic(tmp_path, None)
        monkeypatch.setattr(sys, "argv", ["reply_threads.py", "--selected", selected])
        monkeypatch.setattr(REPLY, "run", _fake_run(ran, [], {}))

        assert REPLY.main() == 0

        assert capsys.readouterr().out == "No review threads selected\n"
        assert ran == []

    @pytest.mark.parametrize("selected", ["T1,T2", '"T1"', "[1]"])
    def test_main_rejects_malformed_selection(
        self, selected: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["reply_threads.py", "--selected", selected])

        assert REPLY.main() == 1

        assert capsys.readouterr().err == "reply_threads.py: --selected must be a JSON array of feedback ids\n"


def _setup(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcomes: dict[str, object] | None,
    selected: str,
    fake_run: Callable[..., str],
) -> None:
    """Write the .agentic/ inputs under root, run from it with --selected and answer commands through fake_run."""
    monkeypatch.chdir(root)
    _write_agentic(root, outcomes)
    monkeypatch.setattr(sys, "argv", ["reply_threads.py", "--selected", selected])
    monkeypatch.setattr(REPLY, "run", fake_run)


def _fake_run(
    ran: list[tuple[str, ...]],
    posted: list[tuple[str, ...]],
    threads: dict[str, dict[str, object]],
    *,
    failing: tuple[str, str] | None = None,
) -> Callable[..., str]:
    """Return a `run` double answering one reply round on PR #7, recording each command in ran.

    `git rev-parse --short HEAD` answers COMMIT and the viewer lookup `me`. The replies query answers the
    thread's node from threads, or an unresolved thread with someone else's comment. Each reply mutation is
    recorded in posted as ("reply", thread, body) and each resolve as ("resolve", thread). failing names a
    (call, thread) pair, call being `reply`, `resolve` or `replies_query`, that raises CalledProcessError.
    """

    def fake_run(*args: str) -> str:
        ran.append(args)
        if args[0] == "git":
            assert args == ("git", "rev-parse", "--short", "HEAD"), f"ran {args}"
            return COMMIT
        if args[-3:] == ("user", "--jq", ".login"):
            return "me"
        call, thread, fields = _graphql_call(args)
        if failing == (call, thread):
            raise subprocess.CalledProcessError(1, list(args), stderr="HTTP 502\n")
        if call == "replies_query":
            return json.dumps(threads.get(thread, _thread()))
        posted.append((call, thread, *fields))
        return ""

    return fake_run


def _graphql_call(args: tuple[str, ...]) -> tuple[str, str, list[str]]:
    """Return which GraphQL call args runs, its thread id and the bodies of any further `-f` fields."""
    assert args[:5] == ("gh", "api", "graphql", "--hostname", "github.com"), f"ran {args}"
    calls = {
        f"query={REPLY.REPLIES_QUERY}": "replies_query",
        f"query={REPLY.REPLY_MUTATION}": "reply",
        f"query={REPLY.RESOLVE_MUTATION}": "resolve",
    }
    fields = [args[index + 1] for index, arg in enumerate(args) if arg == "-f"]
    thread = fields[1].removeprefix("thread=")
    return calls[fields[0]], thread, [field.removeprefix("body=") for field in fields[2:]]
