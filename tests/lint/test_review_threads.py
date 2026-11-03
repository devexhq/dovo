"""Tier 1 contract: scripts/review_threads.py rebuilds a pull request's review threads from REST review comments, replies on them and resolves them."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from collections.abc import Callable
from types import ModuleType

import pytest

from tests.lint.astlib import REPO_ROOT

PR_URL = "https://github.com/o/r/pull/7"
COMMIT = "c" * 40


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
THREADS = _load_script("review_threads", "review_threads_under_test")


def _comment(comment_id: int, login: str, body: str, reply_to: int | None = None) -> dict[str, object]:
    """Return a list_review_comments entry on src/a.py, replying to reply_to when given."""
    return {
        "id": comment_id,
        "in_reply_to_id": reply_to,
        "path": "src/a.py",
        "line": 3,
        "original_line": 4,
        "original_start_line": 2,
        "original_commit_id": COMMIT,
        "login": login,
        "body": body,
        "url": f"https://x/{comment_id}",
    }


def _node(comment_id: int, login: str, body: str) -> dict[str, object]:
    """Return the thread comment node expected for _comment(comment_id, login, body)."""
    return {
        "author": {"login": login},
        "body": body,
        "url": f"https://x/{comment_id}",
        "originalCommit": {"oid": COMMIT},
    }


COMMENTS = [
    _comment(20, "alice", "Second thread."),
    _comment(10, "coderabbitai[bot]", "First thread."),
    _comment(11, "me", "Reply.", reply_to=10),
    _comment(30, "bob", "Orphan reply.", reply_to=99),
]
THREAD_10 = {
    "id": "10",
    "isResolved": True,
    "path": "src/a.py",
    "line": 3,
    "originalStartLine": 2,
    "originalLine": 4,
    "comments": {"nodes": [_node(10, "coderabbitai[bot]", "First thread."), _node(11, "me", "Reply.")]},
}
THREAD_20 = {
    **THREAD_10,
    "id": "20",
    "isResolved": False,
    "comments": {"nodes": [_node(20, "alice", "Second thread.")]},
}
STATES = [{"id": "PRRT_10", "isResolved": True, "root": 10}, {"id": "PRRT_20", "isResolved": False, "root": 20}]


class ReviewThreadsTests:
    def test_group_threads(self) -> None:
        threads = THREADS.group_threads(sorted(COMMENTS, key=lambda comment: int(str(comment["id"]))), {10})

        assert threads == [THREAD_10, THREAD_20]

    def test_list_threads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ran: list[tuple[str, ...]] = []
        monkeypatch.setattr(THREADS, "run", _fake_run(ran))

        assert THREADS.list_threads("github.com", "o/r", 7) == [THREAD_10, THREAD_20]
        assert ran[0][:6] == ("gh", "api", "--hostname", "github.com", "--paginate", "repos/o/r/pulls/7/comments")

    def test_reply_on_thread(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ran: list[tuple[str, ...]] = []
        monkeypatch.setattr(PUBLISH, "run", _fake_run(ran))

        url = THREADS.reply_on_thread("github.com", "o/r", 7, "10", "Fixed.")

        assert url == "https://x/reply"
        assert ran[0][:7] == (
            "gh",
            "api",
            "--hostname",
            "github.com",
            "-X",
            "POST",
            "repos/o/r/pulls/7/comments/10/replies",
        )

    def test_resolve_thread(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ran: list[tuple[str, ...]] = []
        monkeypatch.setattr(THREADS, "run", _fake_run(ran))

        THREADS.resolve_thread("github.com", "o/r", 7, "20")

        assert "thread=PRRT_20" in ran[-1]
        assert f"query={THREADS.RESOLVE_MUTATION}" in ran[-1]

    def test_resolve_thread_refuses_unknown_thread(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(THREADS, "run", _fake_run([]))

        with pytest.raises(LookupError, match="pull request #7 has no review thread starting at comment 99"):
            THREADS.resolve_thread("github.com", "o/r", 7, "99")

    def test_main_prints_threads(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        fake = _fake_run([])
        monkeypatch.setattr(THREADS, "run", fake)
        monkeypatch.setattr(PUBLISH, "run", fake)
        monkeypatch.setattr(sys, "argv", ["review_threads.py", "--pr", "7"])

        assert THREADS.main() == 0

        assert json.loads(capsys.readouterr().out) == [THREAD_10, THREAD_20]

    def test_main_reports_failed_command(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = _fake_run([], failing=True)
        monkeypatch.setattr(THREADS, "run", fake)
        monkeypatch.setattr(PUBLISH, "run", fake)
        monkeypatch.setattr(sys, "argv", ["review_threads.py", "--pr", "7"])

        assert THREADS.main() == 1

        err = capsys.readouterr().err
        assert err.startswith(
            "review_threads.py: command failed: gh api --hostname github.com --paginate repos/o/r/pulls/7/comments"
        )
        assert err.endswith("HTTP 502\n\n")

    def test_cloud_backend_lists_resolved_threads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ran: list[tuple[str, ...]] = []
        monkeypatch.setenv("DOVO_GITHUB_THREADS", "claude-cloud")
        monkeypatch.setattr(THREADS, "run", _fake_run(ran))

        assert THREADS.list_threads("github.com", "o/r", 7) == [THREAD_10, THREAD_20]
        assert ran[-1] == (
            "gh",
            "api",
            "--hostname",
            "github.com",
            "repos/o/r/pulls/7/ccr/review_threads",
            "--jq",
            ".[] | select(.resolved) | .comment_ids[]",
        )
        assert not [args for args in ran if "graphql" in args]

    def test_cloud_backend_resolves_thread(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ran: list[tuple[str, ...]] = []
        monkeypatch.setenv("DOVO_GITHUB_THREADS", "claude-cloud")
        monkeypatch.setattr(THREADS, "run", _fake_run(ran))

        THREADS.resolve_thread("github.com", "o/r", 7, "20")

        assert ran == [
            ("gh", "api", "--hostname", "github.com", "-X", "POST", "repos/o/r/pulls/7/ccr/comments/20/resolve")
        ]

    @pytest.mark.parametrize("backend", [pytest.param("graphql", id="graphql"), pytest.param("", id="empty")])
    def test_graphql_backend_by_name_or_default(self, backend: str, monkeypatch: pytest.MonkeyPatch) -> None:
        ran: list[tuple[str, ...]] = []
        monkeypatch.setenv("DOVO_GITHUB_THREADS", backend)
        monkeypatch.setattr(THREADS, "run", _fake_run(ran))

        assert THREADS.resolved_roots("github.com", "o/r", 7) == {10}
        assert ran[0][2] == "graphql"

    def test_main_refuses_unknown_backend(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = _fake_run([])
        monkeypatch.setenv("DOVO_GITHUB_THREADS", "rest")
        monkeypatch.setattr(THREADS, "run", fake)
        monkeypatch.setattr(PUBLISH, "run", fake)
        monkeypatch.setattr(sys, "argv", ["review_threads.py", "--pr", "7"])

        assert THREADS.main() == 1

        assert capsys.readouterr().err == (
            "review_threads.py: DOVO_GITHUB_THREADS='rest' names no review thread backend; use graphql or claude-cloud\n"
        )


def _fake_run(ran: list[tuple[str, ...]], *, failing: bool = False) -> Callable[..., str]:
    """Return a `run` double for PR #7 of o/r recording each command in ran: the pull request read, COMMENTS,
    STATES, a resolve and a reply, and the claude-cloud backend's resolved comment ids (thread 10 and its reply)
    and resolve; failing makes the review comment listing raise CalledProcessError.
    """
    answers = {
        "repos/{owner}/{repo}/pulls/7": json.dumps({"number": 7, "head": COMMIT, "url": PR_URL}),
        "repos/o/r/pulls/7/comments": "\n".join(json.dumps(comment) for comment in COMMENTS),
        f"query={THREADS.STATES_QUERY}": "\n".join(json.dumps(state) for state in STATES),
        f"query={THREADS.RESOLVE_MUTATION}": "true",
        "repos/o/r/pulls/7/ccr/review_threads": "10\n11",
        "repos/o/r/pulls/7/ccr/comments/20/resolve": "",
        "repos/o/r/pulls/7/comments/10/replies": "https://x/reply",
    }

    def fake_run(*args: str) -> str:
        ran.append(args)
        if failing and "repos/o/r/pulls/7/comments" in args:
            raise subprocess.CalledProcessError(1, list(args), stderr="HTTP 502\n")
        return next(answers[arg] for arg in args if arg in answers)

    return fake_run
