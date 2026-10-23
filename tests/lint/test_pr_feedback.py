"""Tier 1 contract: scripts/pr_feedback.py finds a PR's dovo session and collects its open feedback."""

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

HEAD = "a" * 40
SESSION = "blueprint_abba01ee"
PR_URL = "https://github.com/o/r/pull/7"
HEADER = f"<!-- dovo-review -->\nReviewed commit: `{HEAD}`\nVerdict: APPROVE · Action: none · Round: 2\n"


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
FEEDBACK = _load_script("pr_feedback", "pr_feedback_under_test")


def _thread(
    body: str, *, resolved: bool = False, author: dict[str, object] | None = None, line: int | None = 9
) -> dict[str, object]:
    """Return a GraphQL review thread node whose first comment has body and author."""
    return {
        "isResolved": resolved,
        "path": "scripts/publish_review.py",
        "line": line,
        "comments": {"nodes": [{"author": author, "body": body, "url": f"https://x/{body[:3]}"}]},
    }


def _finding(finding_id: str, severity: str) -> dict[str, object]:
    """Return a review.json finding with finding_id and severity."""
    return {
        "id": finding_id,
        "severity": severity,
        "path": "src/a.py",
        "line": 3,
        "rule": "R4",
        "summary": f"{finding_id} summary",
    }


FINDINGS = [_finding("B1", "BLOCKER"), _finding("W1", "WARNING"), _finding("S1", "SUGGESTION"), _finding("N1", "NIT")]


class PrFeedbackTests:
    @pytest.mark.parametrize(
        ("view", "expected"),
        [
            pytest.param({"state": "OPEN", "isCrossRepository": False}, None, id="open"),
            pytest.param({"state": "CLOSED"}, "pull request #7 is not open", id="closed"),
            pytest.param({"state": "MERGED"}, "pull request #7 is not open", id="merged"),
            pytest.param(
                {"state": "OPEN", "isCrossRepository": True},
                "pull request #7 comes from another repository; pr-fix pushes only to origin",
                id="cross-repository",
            ),
        ],
    )
    def test_pr_problem(self, view: dict[str, object], expected: str | None) -> None:
        assert FEEDBACK.pr_problem(view, 7) == expected

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            pytest.param(f"{HEADER}Dovo session: `{SESSION}`\n\n## report\n", SESSION, id="four-line-header"),
            pytest.param(f"{HEADER}\n## report\n", None, id="three-line-header"),
            pytest.param(f"{HEADER}\nDovo session: `{SESSION}`\n", None, id="session-text-in-report"),
            pytest.param(f"{HEADER}Dovo session: `{SESSION}`\n\n".replace("\n", "\r\n"), SESSION, id="crlf"),
        ],
    )
    def test_session_from_comment(self, body: str, expected: str | None) -> None:
        assert FEEDBACK.session_from_comment(body) == expected

    @pytest.mark.parametrize(
        ("comments", "expected"),
        [
            pytest.param(
                [{"id": 1, "user": "me", "body": f"{HEADER}Dovo session: `{SESSION}`\n\nx"}],
                SESSION,
                id="own-with-session",
            ),
            pytest.param(
                [],
                "no dovo-review comment on pull request #7; run /dovo-review 7 --post first",
                id="no-comment",
            ),
            pytest.param(
                [{"id": 1, "user": "other", "body": f"{HEADER}Dovo session: `{SESSION}`\n\nx"}],
                "no dovo-review comment on pull request #7; run /dovo-review 7 --post first",
                id="other-author-only",
            ),
            pytest.param(
                [{"id": 1, "user": "me", "body": f"{HEADER}\nx"}],
                "the dovo-review comment on pull request #7 names no dovo session",
                id="no-session-line",
            ),
        ],
    )
    def test_comment_session(self, comments: list[dict[str, object]], expected: str) -> None:
        if expected == SESSION:
            assert FEEDBACK.comment_session(comments, "me", 7) == SESSION
            return
        with pytest.raises(FEEDBACK.PrFeedbackError) as raised:
            FEEDBACK.comment_session(comments, "me", 7)
        assert str(raised.value) == expected

    def test_artifact_problem(self) -> None:
        assert FEEDBACK.artifact_problem(SESSION, "plan") == (
            f"cannot restore artifact 'plan' from dovo session {SESSION}; "
            f"check it with dovo artifacts list --session {SESSION}"
        )

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            pytest.param("Reject incomplete records.\nMore detail.", "Reject incomplete records.", id="plain"),
            pytest.param(
                "**Reject** _incomplete_ `review.json` records",
                "Reject incomplete review.json records",
                id="emphasis-and-code",
            ),
            pytest.param("See [the docs](https://x.y/z) now", "See the docs now", id="link"),
            pytest.param(
                "<!-- This is an auto-generated comment\nspanning lines -->\n\n<details>\nReal text",
                "Real text",
                id="leading-html-comment",
            ),
            pytest.param("## Heading\n- item", "Heading", id="heading-and-list"),
            pytest.param("x" * 250, "x" * 200, id="over-200"),
        ],
    )
    def test_thread_summary(self, body: str, expected: str) -> None:
        assert FEEDBACK.thread_summary(body) == expected

    def test_thread_items(self) -> None:
        threads = [
            _thread("Fix one", author={"__typename": "Bot", "login": "coderabbitai"}),
            _thread("Done already", resolved=True, author={"__typename": "User", "login": "alice"}),
            _thread("Fix two", author=None, line=None),
            _thread("Fix three", author={"__typename": "User", "login": "alice"}),
        ]

        items = FEEDBACK.thread_items(threads)

        assert items == [
            {
                "id": "T1",
                "source": "review_thread",
                "author": "coderabbitai[bot]",
                "url": "https://x/Fix",
                "path": "scripts/publish_review.py",
                "line": 9,
                "summary": "Fix one",
                "body": "Fix one",
            },
            {
                "id": "T2",
                "source": "review_thread",
                "author": "ghost",
                "url": "https://x/Fix",
                "path": "scripts/publish_review.py",
                "line": None,
                "summary": "Fix two",
                "body": "Fix two",
            },
            {
                "id": "T3",
                "source": "review_thread",
                "author": "alice",
                "url": "https://x/Fix",
                "path": "scripts/publish_review.py",
                "line": 9,
                "summary": "Fix three",
                "body": "Fix three",
            },
        ]

    @pytest.mark.parametrize(
        ("review", "expected_ids"),
        [
            pytest.param({"verdict": "CHANGES_REQUIRED", "findings": FINDINGS}, ["R1", "R2"], id="changes-required"),
            pytest.param({"verdict": "APPROVE", "findings": FINDINGS}, [], id="approve"),
            pytest.param({"verdict": "CHANGES_REQUIRED"}, [], id="no-findings"),
        ],
    )
    def test_finding_items(self, review: dict[str, object], expected_ids: list[str]) -> None:
        items = FEEDBACK.finding_items(review)

        assert [item["id"] for item in items] == expected_ids
        if expected_ids:
            assert items[0] == {
                "id": "R1",
                "source": "review_finding",
                "author": None,
                "url": None,
                "path": "src/a.py",
                "line": 3,
                "summary": "B1 summary",
                "body": "B1: B1 summary",
            }
            assert items[1]["body"] == "W1: W1 summary"

    def test_feedback_document(self) -> None:
        items = FEEDBACK.thread_items(
            [_thread("Fix one", author={"__typename": "Bot", "login": "coderabbitai"})]
        ) + FEEDBACK.finding_items({"verdict": "CHANGES_REQUIRED", "findings": FINDINGS[:1]})

        document = FEEDBACK.feedback_document(1121, HEAD, SESSION, items)

        assert document == {
            "pr": 1121,
            "head": HEAD,
            "session": SESSION,
            "items": [
                {
                    "id": "T1",
                    "source": "review_thread",
                    "author": "coderabbitai[bot]",
                    "url": "https://x/Fix",
                    "path": "scripts/publish_review.py",
                    "line": 9,
                    "summary": "Fix one",
                    "body": "Fix one",
                    "label": "T1 [review_thread, coderabbitai[bot]] Fix one",
                },
                {
                    "id": "R1",
                    "source": "review_finding",
                    "author": None,
                    "url": None,
                    "path": "src/a.py",
                    "line": 3,
                    "summary": "B1 summary",
                    "body": "B1: B1 summary",
                    "label": "R1 [review_finding, dovo-review] B1 summary",
                },
            ],
        }

    @pytest.mark.parametrize(
        "pr_view",
        [
            pytest.param(
                subprocess.CalledProcessError(1, ["gh", "pr", "view"], stderr="no pull requests"), id="nonexistent"
            ),
            pytest.param(
                json.dumps(
                    {
                        "number": 7,
                        "state": "CLOSED",
                        "headRefName": "b",
                        "headRefOid": HEAD,
                        "url": "https://github.com/o/r/pull/7",
                    }
                ),
                id="closed",
            ),
        ],
    )
    def test_main_reports_pr_not_open(
        self, pr_view: object, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def fake_run(*args: str) -> str:
            assert args[:3] == ("gh", "pr", "view"), f"ran {args}"
            if isinstance(pr_view, BaseException):
                raise pr_view
            return str(pr_view)

        monkeypatch.setattr(sys, "argv", ["pr_feedback.py", "--pr", "7"])
        monkeypatch.setattr(FEEDBACK, "run", fake_run)

        assert FEEDBACK.main() == 1
        assert capsys.readouterr().err == "pr_feedback.py: pull request #7 is not open\n"

    def test_main_reports_artifact_problem(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ran: list[tuple[str, ...]] = []
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["pr_feedback.py", "--pr", "7"])
        fake_run = _fake_run(ran, failing_artifact="review")
        monkeypatch.setattr(FEEDBACK, "run", fake_run)
        monkeypatch.setattr(PUBLISH, "run", fake_run)

        assert FEEDBACK.main() == 1
        assert capsys.readouterr().err == f"pr_feedback.py: {FEEDBACK.artifact_problem(SESSION, 'review')}\n"
        assert not [args for args in ran if args[0] == "git"]
        assert not (tmp_path / ".agentic" / "feedback.json").exists()

    def test_main_restores_and_collects(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ran: list[tuple[str, ...]] = []
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["pr_feedback.py", "--pr", "7"])
        fake_run = _fake_run(ran)
        monkeypatch.setattr(FEEDBACK, "run", fake_run)
        monkeypatch.setattr(PUBLISH, "run", fake_run)

        assert FEEDBACK.main() == 0

        agentic = tmp_path / ".agentic"
        assert capsys.readouterr().out == (
            f"Restored dovo session {SESSION} and collected 2 feedback items for pull request #7\n"
        )
        document = json.loads((agentic / "feedback.json").read_text(encoding="utf-8"))
        assert (document["pr"], document["head"], document["session"]) == (7, HEAD, SESSION)
        assert [item["label"] for item in document["items"]] == [
            "T1 [review_thread, alice] Fix one",
            "R1 [review_finding, dovo-review] B1 summary",
        ]
        assert (agentic / "pr-branch").read_text(encoding="utf-8") == "alice/fix\n"
        assert (agentic / "pr-url").read_text(encoding="utf-8") == f"{PR_URL}\n"
        assert ("git", "switch", "-q", "--detach", HEAD) in ran


def _fake_run(ran: list[tuple[str, ...]], *, failing_artifact: str | None = None) -> Callable[..., str]:
    """Return a `run` double answering the gh, dovo and git calls of one restore of PR #7, recording each in ran.

    Downloading the `review` artifact writes a CHANGES_REQUIRED .agentic/review.json, and downloading
    failing_artifact raises CalledProcessError.
    """

    def fake_run(*args: str) -> str:
        ran.append(args)
        if args[:3] == ("gh", "pr", "view"):
            view = {"number": 7, "state": "OPEN", "headRefName": "alice/fix", "headRefOid": HEAD}
            return json.dumps({**view, "url": PR_URL, "isCrossRepository": False})
        if args[-3:] == ("user", "--jq", ".login"):
            return "me"
        if "repos/o/r/issues/7/comments" in args:
            return json.dumps({"id": 1, "user": "me", "body": f"{HEADER}Dovo session: `{SESSION}`\n\nx"})
        if args[:5] == ("uv", "run", "dovo", "artifacts", "download"):
            return _download(args, failing_artifact)
        if args[:3] == ("gh", "api", "graphql"):
            return json.dumps(_thread("Fix one", author={"__typename": "User", "login": "alice"}))
        assert args[0] == "git", f"ran {args}"
        return ""

    return fake_run


def _download(args: tuple[str, ...], failing_artifact: str | None) -> str:
    """Answer a `dovo artifacts download` call, writing review.json for `review` and failing for failing_artifact."""
    name = args[6]
    if name == failing_artifact:
        raise subprocess.CalledProcessError(1, list(args), stderr="no such artifact")
    if name == "review":
        review = {"verdict": "CHANGES_REQUIRED", "findings": FINDINGS[:1]}
        Path(".agentic").mkdir(exist_ok=True)
        Path(".agentic/review.json").write_text(json.dumps(review), encoding="utf-8")
    return ""
