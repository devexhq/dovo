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
    body: str,
    *,
    resolved: bool = False,
    author: dict[str, object] | None = None,
    line: int | None = 9,
    thread_id: str = "PRRT_1",
) -> dict[str, object]:
    """Return a GraphQL review thread node with thread_id whose first comment has body and author."""
    return {
        "id": thread_id,
        "isResolved": resolved,
        "path": "scripts/publish_review.py",
        "line": line,
        "comments": {"nodes": [{"author": author, "body": body, "url": f"https://x/{body[:3]}"}]},
    }


def _dovo_thread(
    thread_id: str, comments: list[tuple[str, str]], *, resolved: bool = False, line: int = 3
) -> dict[str, object]:
    """Return a review thread node on src/a.py at line whose comments are (login, body) pairs, in order."""
    nodes = [
        {"author": {"__typename": "User", "login": login}, "body": body, "url": f"https://x/{thread_id}/{index}"}
        for index, (login, body) in enumerate(comments)
    ]
    return {"id": thread_id, "isResolved": resolved, "path": "src/a.py", "line": line, "comments": {"nodes": nodes}}


DOVO_REVIEW_URL = f"{PR_URL}#pullrequestreview-11"
LATER_REVIEW_URL = f"{PR_URL}#pullrequestreview-12"


def _finding(
    finding_id: str, severity: str, path: str | None, line: int | None, rule: str | None, **overrides: object
) -> dict[str, object]:
    """Return a review.json finding with finding_id, severity, location and rule, then overrides."""
    finding: dict[str, object] = {
        "id": finding_id,
        "severity": severity,
        "path": path,
        "start_line": None,
        "line": line,
        "rule": rule,
        "summary": f"{finding_id} summary.",
        "issue": f"{finding_id} issue.",
        "fix": "Fix.",
        "also_affects": [],
    }
    finding.update(overrides)
    return finding


B1 = _finding(
    "B1",
    "BLOCKER",
    "src/a.py",
    3,
    "R4",
    start_line=2,
    issue="`Session.details` lets it escape.\nSecond line.",
    fix="Catch it **once**.",
)
N1 = _finding("N1", "NIT", "docs/x.md", None, None, issue="Typo.", fix="Fix.")


def _dovo_review(
    head: str,
    findings: list[dict[str, object]],
    submitted: str = "2026-10-19T09:00:00Z",
    login: str = "me",
    url: str = DOVO_REVIEW_URL,
) -> dict[str, object]:
    """Return a REVIEWS_QUERY node by login on head, submitted at submitted, whose body is review_body of findings."""
    return {
        "author": {"__typename": "User", "login": login},
        "body": PUBLISH.review_body(findings),
        "url": url,
        "submittedAt": submitted,
        "lastEditedAt": None,
        "commit": {"oid": head},
        "comments": {"nodes": []},
    }


def _payload(
    findings: list[dict[str, object]], hunks: tuple[frozenset[int], ...] = (frozenset({3}),)
) -> dict[str, object]:
    """Return publish_review's review payload on HEAD for findings, with hunks the diff hunks of src/a.py."""
    payload = PUBLISH.review_payload({"head": HEAD, "round": 1, "findings": findings}, {"src/a.py": hunks})
    assert payload is not None
    return payload


def _posted(payload: dict[str, object], url: str, submitted: str) -> dict[str, object]:
    """Return the viewer's REVIEWS_QUERY node for payload as posted at url and submitted."""
    comments = payload["comments"]
    assert isinstance(comments, list)
    return {
        **_dovo_review(HEAD, [], submitted=submitted, url=url),
        "body": payload["body"],
        "comments": {"nodes": [{"body": comment["body"]} for comment in comments]},
    }


def _own_comment(findings_line: str) -> dict[str, object]:
    """Return the viewer's dovo-review comment naming SESSION, whose report holds findings_line."""
    return {**OWN_COMMENT, "body": f"{HEADER}Dovo session: `{SESSION}`\n\nx\n\n{findings_line}\n"}


B1_BLOCK = (
    "#### B1 · `src/a.py:2-3` · R4\n\n"
    "**Issue:** `Session.details` lets it escape.\nSecond line.\n\n**Fix:** Catch it **once**.\n\n"
    "<!-- dovo-finding id=B1 severity=BLOCKER rule=R4 -->"
)
B1_SUMMARY = "Session.details lets it escape. Second line."
N1_BLOCK = (
    "#### N1 · `docs/x.md`\n\n**Issue:** Typo.\n\n**Fix:** Fix.\n\n<!-- dovo-finding id=N1 severity=NIT rule=- -->"
)
B1_FINDING = {"id": "B1", "severity": "BLOCKER", "rule": "R4"}
MARKER_FIRST = "<!-- dovo-finding id=W9 severity=WARNING rule=R4 -->"
CUTOFF = "2026-10-20T12:00:00Z"
LINKED_LINE = f"**Findings:** 1 blocking · 0 warnings · 0 suggestions · 0 nits · [review]({DOVO_REVIEW_URL})"
OWN_COMMENT: dict[str, object] = {
    "id": 1,
    "user": "me",
    "body": f"{HEADER}Dovo session: `{SESSION}`\n\nx\n\n{LINKED_LINE}\n",
    "url": "https://x/c1",
    "created_at": "2026-10-19T09:00:00Z",
    "updated_at": CUTOFF,
}
OTHER_COMMENT: dict[str, object] = {
    "id": 2,
    "user": "coderabbitai[bot]",
    "body": "<!-- walkthrough -->\n## Walkthrough\nAdds comment items.",
    "url": "https://x/c2",
    "created_at": "2026-10-20T13:00:00Z",
    "updated_at": "2026-10-20T13:00:00Z",
}


def _comment(comment_id: int, user: str | None, body: str, created: str, updated: str) -> dict[str, object]:
    """Return a list_comments entry with comment_id, user, body and creation and update times."""
    return {
        "id": comment_id,
        "user": user,
        "body": body,
        "url": f"https://x/c{comment_id}",
        "created_at": created,
        "updated_at": updated,
    }


def _review(
    body: str, author: dict[str, object] | None, submitted: str | None, edited: str | None = None
) -> dict[str, object]:
    """Return a REVIEWS_QUERY node with body, author and submission and last edit times."""
    return {
        "author": author,
        "body": body,
        "url": f"https://x/r/{body[:3]}",
        "submittedAt": submitted,
        "lastEditedAt": edited,
    }


def _comment_item(item_id: str, author: str, url: str, body: str, summary: str) -> dict[str, object]:
    """Return the `comment` item expected for a comment or review."""
    return {
        "id": item_id,
        "source": "comment",
        "thread": None,
        "author": author,
        "url": url,
        "path": None,
        "line": None,
        "summary": summary,
        "body": body,
        "finding": None,
    }


def _check_item(item_id: str, name: str, conclusion: str, url: str) -> dict[str, object]:
    """Return the `check` item expected for a failed check run."""
    return {
        "id": item_id,
        "source": "check",
        "thread": None,
        "author": None,
        "url": url,
        "path": None,
        "line": None,
        "summary": f"{name}: {conclusion}",
        "body": f"{name}: {conclusion}",
        "finding": None,
    }


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
            pytest.param([OTHER_COMMENT, OWN_COMMENT], OWN_COMMENT, id="own-comment"),
            pytest.param(
                [],
                "no dovo-review comment on pull request #7; run /dovo-review 7 --post first",
                id="no-comment",
            ),
            pytest.param(
                [{**OWN_COMMENT, "user": "other"}],
                "no dovo-review comment on pull request #7; run /dovo-review 7 --post first",
                id="other-author-only",
            ),
        ],
    )
    def test_review_comment(self, comments: list[dict[str, object]], expected: object) -> None:
        if isinstance(expected, dict):
            assert FEEDBACK.review_comment(comments, "me", 7) == expected
            return
        with pytest.raises(FEEDBACK.PrFeedbackError) as raised:
            FEEDBACK.review_comment(comments, "me", 7)
        assert str(raised.value) == expected

    @pytest.mark.parametrize(
        ("comment", "expected"),
        [
            pytest.param(OWN_COMMENT, SESSION, id="with-session"),
            pytest.param(
                {**OWN_COMMENT, "body": f"{HEADER}\nx"},
                "the dovo-review comment on pull request #7 names no dovo session",
                id="no-session-line",
            ),
        ],
    )
    def test_comment_session(self, comment: dict[str, object], expected: str) -> None:
        if expected == SESSION:
            assert FEEDBACK.comment_session(comment, 7) == SESSION
            return
        with pytest.raises(FEEDBACK.PrFeedbackError) as raised:
            FEEDBACK.comment_session(comment, 7)
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
    def test_markdown_summary(self, body: str, expected: str) -> None:
        assert FEEDBACK.markdown_summary(body) == expected

    def test_thread_items(self) -> None:
        threads = [
            _thread("Fix one", author={"__typename": "Bot", "login": "coderabbitai"}),
            _thread("Done already", resolved=True, author={"__typename": "User", "login": "alice"}),
            _thread("Fix two", author=None, line=None),
            _thread("Fix three", author={"__typename": "User", "login": "alice"}),
            _dovo_thread(
                "PRRT_9",
                [
                    ("me", f"{PUBLISH.inline_body(B1)}\n"),
                    ("alice", "Quoted.\n<!-- dovo-finding id=X1 severity=NIT rule=- -->"),
                    ("me", "Another note.\n<!-- dovo-finding id=W2 severity=WARNING rule=R4 -->"),
                ],
            ),
            _dovo_thread("PRRT_8", [("bob", N1_BLOCK)]),
            _dovo_thread("PRRT_7", [("me", f"{MARKER_FIRST}\nMarker first.")]),
            _dovo_thread("PRRT_6", [("me", f"Plain note.\n{MARKER_FIRST}")]),
        ]

        items = FEEDBACK.thread_items(threads, "me")

        assert items == [
            {
                "id": "T1",
                "source": "review_thread",
                "thread": "PRRT_1",
                "author": "coderabbitai[bot]",
                "url": "https://x/Fix",
                "path": "scripts/publish_review.py",
                "line": 9,
                "summary": "Fix one",
                "body": "Fix one",
                "finding": None,
            },
            {
                "id": "T2",
                "source": "review_thread",
                "thread": "PRRT_1",
                "author": "ghost",
                "url": "https://x/Fix",
                "path": "scripts/publish_review.py",
                "line": None,
                "summary": "Fix two",
                "body": "Fix two",
                "finding": None,
            },
            {
                "id": "T3",
                "source": "review_thread",
                "thread": "PRRT_1",
                "author": "alice",
                "url": "https://x/Fix",
                "path": "scripts/publish_review.py",
                "line": 9,
                "summary": "Fix three",
                "body": "Fix three",
                "finding": None,
            },
            {
                "id": "T4",
                "source": "review_thread",
                "thread": "PRRT_9",
                "author": "me",
                "url": "https://x/PRRT_9/0",
                "path": "src/a.py",
                "line": 3,
                "summary": "B1 summary.",
                "body": f"{PUBLISH.inline_body(B1)}\n",
                "finding": B1_FINDING,
            },
            {
                "id": "T5",
                "source": "review_thread",
                "thread": "PRRT_8",
                "author": "bob",
                "url": "https://x/PRRT_8/0",
                "path": "src/a.py",
                "line": 3,
                "summary": "Typo.",
                "body": N1_BLOCK,
                "finding": None,
            },
            {
                "id": "T6",
                "source": "review_thread",
                "thread": "PRRT_7",
                "author": "me",
                "url": "https://x/PRRT_7/0",
                "path": "src/a.py",
                "line": 3,
                "summary": "Marker first.",
                "body": f"{MARKER_FIRST}\nMarker first.",
                "finding": None,
            },
            {
                "id": "T7",
                "source": "review_thread",
                "thread": "PRRT_6",
                "author": "me",
                "url": "https://x/PRRT_6/0",
                "path": "src/a.py",
                "line": 3,
                "summary": "Plain note.",
                "body": f"Plain note.\n{MARKER_FIRST}",
                "finding": {"id": "W9", "severity": "WARNING", "rule": "R4"},
            },
        ]

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            pytest.param(str(OWN_COMMENT["body"]), DOVO_REVIEW_URL, id="linked"),
            pytest.param(str(OWN_COMMENT["body"]).replace("\n", "\r\n"), DOVO_REVIEW_URL, id="crlf"),
            pytest.param(str(_own_comment("**Findings:** none")["body"]), None, id="findings-none"),
            pytest.param(
                str(_own_comment("**Findings:** 1 blocking · 0 warnings · 0 suggestions · 0 nits")["body"]),
                None,
                id="unlinked",
            ),
            pytest.param(
                str(_own_comment(LINKED_LINE.replace("[review]", "[round 1 review]"))["body"]),
                None,
                id="old-round-link",
            ),
        ],
    )
    def test_linked_review(self, body: str, expected: str | None) -> None:
        assert FEEDBACK.linked_review(body) == expected

    def test_finding_items(self) -> None:
        items = FEEDBACK.finding_items(_dovo_review(HEAD, [B1, N1]))

        assert items == [
            {
                "id": "R1",
                "source": "review_finding",
                "thread": None,
                "author": None,
                "url": DOVO_REVIEW_URL,
                "path": "src/a.py",
                "line": 3,
                "summary": B1_SUMMARY,
                "body": B1_BLOCK,
                "finding": B1_FINDING,
            },
            {
                "id": "R2",
                "source": "review_finding",
                "thread": None,
                "author": None,
                "url": DOVO_REVIEW_URL,
                "path": "docs/x.md",
                "line": None,
                "summary": "Typo.",
                "body": N1_BLOCK,
                "finding": {"id": "N1", "severity": "NIT", "rule": None},
            },
        ]

    def test_finding_items_keeps_heading_line_in_issue(self) -> None:
        finding = _finding("W1", "WARNING", "docs/x.md", 10, "R17", issue="The page says\n#### Usage\nwhich is stale.")

        [item] = FEEDBACK.finding_items(_dovo_review(HEAD, [finding]))

        assert (item["path"], item["line"], item["summary"]) == (
            "docs/x.md",
            10,
            "The page says #### Usage which is stale.",
        )

    def test_finding_items_without_review(self) -> None:
        assert FEEDBACK.finding_items(None) == []

    def test_collects_what_publish_posts(self) -> None:
        findings = [
            _finding("B1", "BLOCKER", "src/a.py", 3, "R4"),
            _finding("S1", "SUGGESTION", "src/a.py", 12, "R12", start_line=10),
            _finding("W1", "WARNING", "src/a.py", 40, "R12", start_line=38),
        ]
        payload = _payload(findings, (frozenset({3}), frozenset({10, 11, 12})))
        comments = payload["comments"]
        assert isinstance(comments, list)
        threads = [
            _dovo_thread(f"PRRT_{index}", [("me", comment["body"])], line=comment["line"])
            for index, comment in enumerate(comments)
        ]

        items = FEEDBACK.thread_items(threads, "me") + FEEDBACK.finding_items(_posted(payload, DOVO_REVIEW_URL, CUTOFF))

        assert comments[1]["start_line"] == 10
        assert [(item["id"], item["finding"]["id"], item["path"], item["line"], item["summary"]) for item in items] == [
            ("T1", "B1", "src/a.py", 3, "B1 summary."),
            ("T2", "S1", "src/a.py", 12, "S1 summary."),
            ("R1", "W1", "src/a.py", 40, "W1 issue."),
        ]

    def test_comment_items(self) -> None:
        comments = [
            OWN_COMMENT,
            _comment(3, "alice", "Old note", "2026-10-18T08:00:00Z", "2026-10-18T08:00:00Z"),
            _comment(4, "alice", "Edited note", "2026-10-18T09:00:00Z", "2026-10-20T14:00:00Z"),
            _comment(5, "bob", "At the cut-off", "2026-10-19T10:00:00Z", CUTOFF),
            OTHER_COMMENT,
            _comment(
                6, "bob", "<!-- only -->\n<details>\n</details>\n", "2026-10-20T15:00:00Z", "2026-10-20T15:00:00Z"
            ),
            _comment(7, None, "Deleted user note", "2026-10-20T16:00:00Z", "2026-10-20T16:00:00Z"),
        ]
        reviews = [
            _review(
                "**Actionable** comments posted: 3", {"__typename": "User", "login": "carol"}, "2026-10-20T12:30:00Z"
            ),
            _review(
                "Edited review", {"__typename": "User", "login": "dave"}, "2026-10-17T08:00:00Z", "2026-10-20T13:30:00Z"
            ),
            _review("Bot review", {"__typename": "Bot", "login": "copilot"}, "2026-10-20T17:00:00Z"),
            _review("", {"__typename": "User", "login": "erin"}, "2026-10-20T18:00:00Z"),
            _review("Pending review", {"__typename": "User", "login": "me"}, None),
        ]

        items = FEEDBACK.comment_items(comments, reviews, "me", CUTOFF)

        assert items == [
            _comment_item("C1", "dave", "https://x/r/Edi", "Edited review", "Edited review"),
            _comment_item("C2", "alice", "https://x/c4", "Edited note", "Edited note"),
            _comment_item(
                "C3", "carol", "https://x/r/**A", "**Actionable** comments posted: 3", "Actionable comments posted: 3"
            ),
            _comment_item("C4", "coderabbitai[bot]", "https://x/c2", str(OTHER_COMMENT["body"]), "Walkthrough"),
            _comment_item("C5", "ghost", "https://x/c7", "Deleted user note", "Deleted user note"),
            _comment_item("C6", "copilot[bot]", "https://x/r/Bot", "Bot review", "Bot review"),
        ]

    def test_check_items(self) -> None:
        checks = [
            {"name": "Tests", "conclusion": "failure", "details_url": "https://ci/tests", "html_url": "https://gh/1"},
            {"name": "Lint", "conclusion": "success", "details_url": "https://ci/lint", "html_url": "https://gh/2"},
            {"name": "Build", "conclusion": "cancelled", "details_url": None, "html_url": "https://gh/3"},
            {"name": "Docs", "conclusion": "neutral", "details_url": "https://ci/docs", "html_url": "https://gh/4"},
            {
                "name": "Tests",
                "conclusion": "timed_out",
                "details_url": "https://ci/tests2",
                "html_url": "https://gh/5",
            },
            {"name": "Audit", "conclusion": "skipped", "details_url": "https://ci/audit", "html_url": "https://gh/6"},
            {"name": "Deploy", "conclusion": None, "details_url": "https://ci/deploy", "html_url": "https://gh/7"},
        ]

        items = FEEDBACK.check_items(checks)

        assert items == [
            _check_item("K1", "Build", "cancelled", "https://gh/3"),
            _check_item("K2", "Tests", "failure", "https://ci/tests"),
            _check_item("K3", "Tests", "timed_out", "https://ci/tests2"),
        ]

    @pytest.mark.parametrize(
        ("item", "expected"),
        [
            pytest.param(
                {"id": "T1", "source": "review_thread", "author": "alice", "summary": "Fix one"},
                "T1 [review_thread, alice] Fix one",
                id="review-thread-author",
            ),
            pytest.param(
                {"id": "R1", "source": "review_finding", "author": None, "summary": "B1 summary"},
                "R1 [review_finding, dovo-review] B1 summary",
                id="review-finding-null",
            ),
            pytest.param(
                {"id": "R1", "source": "review_finding", "author": None, "summary": "Lock", "finding": B1_FINDING},
                "R1 [review_finding, dovo-review, B1 BLOCKER] Lock",
                id="review-finding-dovo",
            ),
            pytest.param(
                {
                    "id": "T1",
                    "source": "review_thread",
                    "author": "me",
                    "summary": "Lock",
                    "finding": {"id": "W3", "severity": "WARNING", "rule": "R4"},
                },
                "T1 [review_thread, me, W3 WARNING] Lock",
                id="review-thread-dovo",
            ),
            pytest.param(
                {"id": "C1", "source": "comment", "author": "coderabbitai[bot]", "summary": "Walkthrough"},
                "C1 [comment, coderabbitai[bot]] Walkthrough",
                id="comment-author",
            ),
            pytest.param(
                {"id": "K1", "source": "check", "author": None, "summary": "Tests: failure"},
                "K1 [check, ci] Tests: failure",
                id="check-null",
            ),
        ],
    )
    def test_option_label(self, item: dict[str, object], expected: str) -> None:
        assert FEEDBACK.option_label(item) == expected

    def test_feedback_document(self) -> None:
        items = FEEDBACK.thread_items(
            [_thread("Fix one", author={"__typename": "Bot", "login": "coderabbitai"})], "me"
        ) + FEEDBACK.finding_items(_dovo_review(HEAD, [B1]))

        document = FEEDBACK.feedback_document(1121, HEAD, SESSION, items)

        assert document == {
            "pr": 1121,
            "head": HEAD,
            "session": SESSION,
            "items": [
                {
                    "id": "T1",
                    "source": "review_thread",
                    "thread": "PRRT_1",
                    "author": "coderabbitai[bot]",
                    "url": "https://x/Fix",
                    "path": "scripts/publish_review.py",
                    "line": 9,
                    "summary": "Fix one",
                    "body": "Fix one",
                    "finding": None,
                    "label": "T1 [review_thread, coderabbitai[bot]] Fix one",
                },
                {
                    "id": "R1",
                    "source": "review_finding",
                    "thread": None,
                    "author": None,
                    "url": DOVO_REVIEW_URL,
                    "path": "src/a.py",
                    "line": 3,
                    "summary": B1_SUMMARY,
                    "body": B1_BLOCK,
                    "finding": B1_FINDING,
                    "label": f"R1 [review_finding, dovo-review, B1 BLOCKER] {B1_SUMMARY}",
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
        self, pr_view: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def fake_run(*args: str) -> str:
            assert args[:3] == ("gh", "pr", "view"), f"ran {args}"
            if isinstance(pr_view, BaseException):
                raise pr_view
            return str(pr_view)

        monkeypatch.chdir(tmp_path)
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
        later = _dovo_review(HEAD, [N1], submitted="2026-10-19T10:00:00Z", url=LATER_REVIEW_URL)
        fake_run = _fake_run(ran, reviews=(later, _dovo_review(HEAD, [B1])))
        monkeypatch.setattr(FEEDBACK, "run", fake_run)
        monkeypatch.setattr(PUBLISH, "run", fake_run)
        agentic = tmp_path / ".agentic"
        agentic.mkdir()
        (agentic / "triage.json").write_text('{"T1": {"action": "dismiss"}}\n', encoding="utf-8")

        assert FEEDBACK.main() == 0

        assert not (agentic / "triage.json").exists()
        assert capsys.readouterr().out == (
            f"Restored dovo session {SESSION} and collected 4 feedback items for pull request #7\n"
        )
        document = json.loads((agentic / "feedback.json").read_text(encoding="utf-8"))
        assert (document["pr"], document["head"], document["session"]) == (7, HEAD, SESSION)
        assert [item["label"] for item in document["items"]] == [
            "T1 [review_thread, alice] Fix one",
            f"R1 [review_finding, dovo-review, B1 BLOCKER] {B1_SUMMARY}",
            "C1 [comment, coderabbitai[bot]] Walkthrough",
            "K1 [check, ci] Tests: failure",
        ]
        assert document["items"][0]["thread"] == "PRRT_1"
        assert (agentic / "pr-branch").read_text(encoding="utf-8") == "alice/fix\n"
        assert (agentic / "pr-url").read_text(encoding="utf-8") == f"{PR_URL}\n"
        assert ("git", "switch", "-q", "--detach", HEAD) in ran
        assert not (agentic / "review.json").exists()

    def test_main_lists_no_findings_when_comment_has_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["pr_feedback.py", "--pr", "7"])
        fake_run = _fake_run(
            [],
            reviews=(_dovo_review(HEAD, [B1]),),
            own_comment=_own_comment("**Findings:** none"),
        )
        monkeypatch.setattr(FEEDBACK, "run", fake_run)
        monkeypatch.setattr(PUBLISH, "run", fake_run)

        assert FEEDBACK.main() == 0

        document = json.loads((tmp_path / ".agentic" / "feedback.json").read_text(encoding="utf-8"))
        assert [item["id"] for item in document["items"]] == ["T1", "C1", "K1"]
        assert capsys.readouterr().out == (
            f"Restored dovo session {SESSION} and collected 3 feedback items for pull request #7\n"
        )

    def test_reads_later_of_two_posts_on_one_head(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        first = _payload([_finding("B1", "BLOCKER", "src/a.py", 3, "R4")])
        later_findings = [_finding("N1", "NIT", "docs/x.md", None, None)]
        later = _payload(later_findings)
        posted_first = _posted(first, DOVO_REVIEW_URL, "2026-10-19T09:00:00Z")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["pr_feedback.py", "--pr", "7"])
        reviews = (posted_first, _posted(later, LATER_REVIEW_URL, "2026-10-19T10:00:00Z"))
        comment = _own_comment(PUBLISH.findings_line(later_findings, LATER_REVIEW_URL))
        fake_run = _fake_run([], reviews=reviews, own_comment=comment)
        monkeypatch.setattr(FEEDBACK, "run", fake_run)
        monkeypatch.setattr(PUBLISH, "run", fake_run)

        assert PUBLISH.matching_review([posted_first], "me", later) is None
        assert FEEDBACK.main() == 0

        document = json.loads((tmp_path / ".agentic" / "feedback.json").read_text(encoding="utf-8"))
        assert [
            (item["id"], item["finding"]["id"], item["url"])
            for item in document["items"]
            if item["source"] == "review_finding"
        ] == [("R1", "N1", LATER_REVIEW_URL)]

    def test_main_lists_findings_after_push(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        pushed = "b" * 40
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["pr_feedback.py", "--pr", "7"])
        on_pushed = _dovo_review(pushed, [N1], submitted="2026-10-19T10:00:00Z", url=LATER_REVIEW_URL)
        reviews = (on_pushed, _dovo_review(HEAD, [B1]))
        fake_run = _fake_run([], reviews=reviews, pr_head=pushed)
        monkeypatch.setattr(FEEDBACK, "run", fake_run)
        monkeypatch.setattr(PUBLISH, "run", fake_run)

        assert FEEDBACK.main() == 0

        document = json.loads((tmp_path / ".agentic" / "feedback.json").read_text(encoding="utf-8"))
        assert document["head"] == pushed
        assert [item["id"] for item in document["items"] if item["source"] == "review_finding"] == ["R1"]
        assert document["items"][1]["finding"] == B1_FINDING

    def test_main_reports_unreadable_checks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["pr_feedback.py", "--pr", "7"])
        fake_run = _fake_run([], failing_checks=True)
        monkeypatch.setattr(FEEDBACK, "run", fake_run)
        monkeypatch.setattr(PUBLISH, "run", fake_run)

        assert FEEDBACK.main() == 1
        assert capsys.readouterr().err == (
            "pr_feedback.py: command failed: gh api --hostname github.com --paginate "
            f"repos/o/r/commits/{HEAD}/check-runs --jq .check_runs[] | {{name, conclusion, details_url, html_url}}\n"
            "HTTP 403\n"
        )
        assert not (tmp_path / ".agentic" / "feedback.json").exists()


def _fake_run(
    ran: list[tuple[str, ...]],
    *,
    reviews: tuple[dict[str, object], ...] = (),
    own_comment: dict[str, object] = OWN_COMMENT,
    pr_head: str = HEAD,
    failing_artifact: str | None = None,
    failing_checks: bool = False,
) -> Callable[..., str]:
    """Return a `run` double answering the gh, dovo and git calls of one restore of PR #7, recording each in ran.

    The pull request's comments are own_comment, the viewer's dovo-review comment, then OTHER_COMMENT. The
    pull request's head is pr_head. Downloading failing_artifact raises CalledProcessError. The GraphQL
    call answers one thread for `reviewThreads` and the reviews nodes for `reviews`; the check-runs call on
    pr_head answers a failed `Tests` and a passed `Prek` run, or raises CalledProcessError when failing_checks is set.
    """

    def fake_run(*args: str) -> str:
        ran.append(args)
        if args[:3] == ("gh", "pr", "view"):
            view = {"number": 7, "state": "OPEN", "headRefName": "alice/fix", "headRefOid": pr_head}
            return json.dumps({**view, "url": PR_URL, "isCrossRepository": False})
        if args[:5] == ("uv", "run", "dovo", "artifacts", "download"):
            return _download(args, failing_artifact)
        if args[:2] == ("gh", "api"):
            return _gh_api(args, reviews, (own_comment, OTHER_COMMENT), pr_head, failing_checks)
        assert args[0] == "git", f"ran {args}"
        return ""

    return fake_run


def _gh_api(
    args: tuple[str, ...],
    reviews: tuple[dict[str, object], ...],
    comments: tuple[dict[str, object], ...],
    pr_head: str,
    failing_checks: bool,
) -> str:
    """Answer a `gh api` call of one restore of PR #7: the viewer, its comments, GraphQL nodes or check runs."""
    if args[-3:] == ("user", "--jq", ".login"):
        return "me"
    if "repos/o/r/issues/7/comments" in args:
        return "\n".join(json.dumps(comment) for comment in comments)
    if args[2] == "graphql":
        if args[-1] == ".data.repository.pullRequest.reviews.nodes[]":
            return "\n".join(json.dumps(review) for review in reviews)
        return json.dumps(_thread("Fix one", author={"__typename": "User", "login": "alice"}))
    assert f"repos/o/r/commits/{pr_head}/check-runs" in args, f"ran {args}"
    return _checks(args, failing_checks)


def _download(args: tuple[str, ...], failing_artifact: str | None) -> str:
    """Answer a `dovo artifacts download` call, creating .agentic/ as a download does and failing for failing_artifact."""
    if args[6] == failing_artifact:
        raise subprocess.CalledProcessError(1, list(args), stderr="no such artifact")
    Path(".agentic").mkdir(exist_ok=True)
    return ""


def _checks(args: tuple[str, ...], failing: bool) -> str:
    """Answer a check-runs call with a failed `Tests` and a passed `Prek` run, or raise CalledProcessError when failing."""
    if failing:
        raise subprocess.CalledProcessError(1, list(args), stderr="HTTP 403")
    runs = [
        {"name": "Tests", "conclusion": "failure", "details_url": "https://ci/tests", "html_url": "https://gh/1"},
        {"name": "Prek", "conclusion": "success", "details_url": "https://ci/prek", "html_url": "https://gh/2"},
    ]
    return "\n".join(json.dumps(check_run) for check_run in runs)
