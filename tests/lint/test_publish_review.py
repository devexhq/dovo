"""Tier 1 contract: scripts/publish_review.py posts the round's pull request review and the PR's one dovo-review comment."""

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

PR_HEAD = "a" * 40
MARKER = "<!-- dovo-pr-fix round 2 -->"
PR_URL = "https://github.com/o/r/pull/7"
REVIEW_URL = f"{PR_URL}#pullrequestreview-11"
COMMENT_URL = f"{PR_URL}#issuecomment-5"
TABLE_HEADER = "| ID | Location | Rule | Issue | Fix |"
SESSION = "blueprint_abba01ee"
COMMENT_HEADER = f"<!-- dovo-review -->\nReviewed commit: `{PR_HEAD}`\nVerdict: APPROVE · Action: none · Round: 2\n"


def _load_publisher() -> ModuleType:
    """Load scripts/publish_review.py by path, since scripts/ is not a package."""
    path = REPO_ROOT / "scripts" / "publish_review.py"
    spec = importlib.util.spec_from_file_location("publish_review_under_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PUBLISHER = _load_publisher()


def _review(head: str | None = PR_HEAD, drop: str | None = None, **overrides: object) -> dict[str, object]:
    """Return a consistent approving review.json payload with head and overrides, minus the drop key when given."""
    review: dict[str, object] = {
        "head": head,
        "verdict": "APPROVE",
        "action": "none",
        "round": 1,
        "counts": {"blocking": 0},
        "findings": [],
    }
    review.update(overrides)
    if drop is not None:
        del review[drop]
    return review


def _finding(
    finding_id: str, severity: str, path: str | None, line: int | None, rule: str | None = "R4", **overrides: object
) -> dict[str, object]:
    """Return a review.json finding with finding_id, severity, location and rule, then overrides."""
    finding: dict[str, object] = {
        "id": finding_id,
        "severity": severity,
        "path": path,
        "start_line": None,
        "line": line,
        "rule": rule,
        "summary": f"{finding_id} summary",
        "issue": f"{finding_id} issue.",
        "fix": f"{finding_id} fix.",
        "also_affects": [],
    }
    finding.update(overrides)
    return finding


FINDINGS = [
    _finding("B1", "BLOCKER", "src/a.py", 3),
    _finding("W1", "WARNING", "src/a.py", 40),
    _finding("N1", "NIT", None, None, rule=None),
]
HUNKS = {"src/a.py": (frozenset({3, 4}), frozenset({10, 11, 12}))}
PATCH = "@@ -1,2 +3,2 @@\n line\n+added\n@@ -8,2 +10,3 @@\n x\n+y\n z"
PROMPT_INTRO = (
    "Review the code at the location below. A potential issue has been identified by an AI agent.\n"
    "Verify if this is a real issue. If it is, propose a fix; if not, explain why it's not valid.\n"
)
SUGGESTED_FIX = "<details><summary><b>Suggested fix</b></summary>"
PROMPT_DETAILS = "<details><summary><b>Prompt for an AI agent</b></summary>"
OUTSIDE_HEAD = (
    "🤖 **Agentic Review**\n\n> [!CAUTION]\n"
    "> Some comments are outside the diff and can't be posted inline due to GitHub limitations.\n\n"
    "<details>\n<summary>⚠️ <b>Outside diff range comments "
)
B1_BLOCK = (
    "#### B1 · `src/a.py:3` · R4\n\n**Issue:** B1 issue.\n\n**Fix:** B1 fix.\n\n"
    "<!-- dovo-finding id=B1 severity=BLOCKER rule=R4 -->"
)
W1_BLOCK = (
    "#### W1 · `src/a.py:40` · R4\n\n**Issue:** W1 issue.\n\n**Fix:** W1 fix.\n\n"
    "<!-- dovo-finding id=W1 severity=WARNING rule=R4 -->"
)
N1_BLOCK = "#### N1\n\n**Issue:** N1 issue.\n\n**Fix:** N1 fix.\n\n<!-- dovo-finding id=N1 severity=NIT rule=- -->"
REPORT_HEAD = f"**Verdict:** CHANGES REQUIRED · **Action:** fix\n**Reviewed commit:** `{PR_HEAD}`\n\nOne blocker.\n\n"
FINDING_TABLES = (
    f"### Blocking\n\n{TABLE_HEADER}\n|---|---|---|---|---|\n| B1 | `src/a.py:3` | R4 | B1 issue. | B1 fix. |\n\n"
)


def _changes_required(**overrides: object) -> dict[str, object]:
    """Return a CHANGES_REQUIRED review.json payload with FINDINGS, then overrides."""
    review = _review(verdict="CHANGES_REQUIRED", action="fix", counts={"blocking": 1}, findings=FINDINGS)
    review.update(overrides)
    return review


def _write_review(root: Path, review: dict[str, object], report: str) -> None:
    """Write review.json and review.md under root/.agentic."""
    agentic = root / ".agentic"
    agentic.mkdir()
    (agentic / "review.json").write_text(json.dumps(review), encoding="utf-8")
    (agentic / "review.md").write_text(report, encoding="utf-8")


def _own_review(
    body: str,
    login: str = "me",
    *,
    inline: tuple[str, ...] = (),
    commit: str = PR_HEAD,
    submitted: str | None = "2026-10-19T09:00:00Z",
    url: str = REVIEW_URL,
) -> dict[str, object]:
    """Return a REVIEWS_QUERY node by login on commit with body, inline comment bodies, submittedAt and url."""
    return {
        "author": {"__typename": "User", "login": login},
        "body": body,
        "url": url,
        "submittedAt": submitted,
        "lastEditedAt": None,
        "commit": {"oid": commit},
        "comments": {"nodes": [{"body": text} for text in inline]},
    }


HEADING = PUBLISHER.REVIEW_HEADING
OLDER_URL = f"{PR_URL}#pullrequestreview-10"
MATCH_REVIEW = _changes_required(findings=[*FINDINGS, _finding("S1", "SUGGESTION", "src/a.py", 4)])


def _payload(review: dict[str, object]) -> dict[str, object]:
    """Return review_payload of review on PATCH's diff, which must post a review."""
    payload = PUBLISHER.review_payload(review, HUNKS)
    assert payload is not None
    return payload


def _inline_bodies(payload: dict[str, object]) -> tuple[str, ...]:
    """Return the bodies of payload's inline comments, in order."""
    comments = payload["comments"]
    assert isinstance(comments, list)
    return tuple(str(comment["body"]) for comment in comments)


def _posted(
    payload: dict[str, object],
    *,
    body: str | None = None,
    inline: tuple[str, ...] | None = None,
    login: str = "me",
    commit: str = PR_HEAD,
    submitted: str | None = "2026-10-19T09:00:00Z",
    url: str = REVIEW_URL,
) -> dict[str, object]:
    """Return login's REVIEWS_QUERY node for payload as posted, with body and inline bodies replaced when given."""
    return _own_review(
        str(payload["body"]) if body is None else body,
        login,
        inline=_inline_bodies(payload) if inline is None else inline,
        commit=commit,
        submitted=submitted,
        url=url,
    )


class PublishReviewTests:
    @pytest.mark.parametrize(
        ("session", "session_line"),
        [
            pytest.param("blueprint_abba01ee", "Dovo session: `blueprint_abba01ee`\n", id="with-session"),
            pytest.param(None, "", id="without-session"),
        ],
    )
    def test_comment_body_stamps_header(self, session: str | None, session_line: str) -> None:
        review = {"head": PR_HEAD, "verdict": "APPROVE", "action": "none", "round": 2}

        body = PUBLISHER.comment_body(review, "## dovo-review - round 2\n", session=session)

        assert body == (
            "<!-- dovo-review -->\n"
            f"Reviewed commit: `{PR_HEAD}`\n"
            "Verdict: APPROVE · Action: none · Round: 2\n"
            f"{session_line}"
            "\n"
            "## dovo-review - round 2\n"
        )

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            pytest.param(f"{COMMENT_HEADER}Dovo session: `{SESSION}`\n\n## report\n", SESSION, id="four-line-header"),
            pytest.param(f"{COMMENT_HEADER}\n## report\n", None, id="three-line-header"),
            pytest.param(f"{COMMENT_HEADER}\nDovo session: `{SESSION}`\n", None, id="session-text-in-report"),
            pytest.param(f"{COMMENT_HEADER}Dovo session: `{SESSION}`\n\n".replace("\n", "\r\n"), SESSION, id="crlf"),
        ],
    )
    def test_session_from_comment(self, body: str, expected: str | None) -> None:
        assert PUBLISHER.session_from_comment(body) == expected

    @pytest.mark.parametrize("session", [pytest.param("", id="empty"), pytest.param("  ", id="blank")])
    def test_main_rejects_empty_session(
        self, session: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def no_run(*args: str) -> str:
            pytest.fail(f"ran {args}")

        monkeypatch.setattr(sys, "argv", ["publish_review.py", "--pr", "7", "--session", session])
        monkeypatch.setattr(PUBLISHER, "run", no_run)

        assert PUBLISHER.main() == 1
        assert capsys.readouterr().err == "publish_review.py: --session must not be empty\n"

    def test_main_stamps_session(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        agentic = tmp_path / ".agentic"
        agentic.mkdir()
        (agentic / "review.json").write_text(json.dumps(_review()), encoding="utf-8")
        (agentic / "review.md").write_text(
            f"**Verdict:** APPROVE · **Action:** none\n**Reviewed commit:** `{PR_HEAD}`\n", encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["publish_review.py", "--pr", "7", "--session", "blueprint_abba01ee"])
        posted: list[str] = []

        def fake_run(*args: str) -> str:
            if args[:3] == ("gh", "pr", "view"):
                return json.dumps({"number": 7, "headRefOid": PR_HEAD, "url": "https://github.com/o/r/pull/7"})
            if "user" in args:
                return "me"
            body_arg = next((arg for arg in args if arg.startswith("body=@")), None)
            if body_arg is None:
                return ""
            posted.append(Path(body_arg.removeprefix("body=@")).read_text(encoding="utf-8"))
            return "https://github.com/o/r/pull/7#issuecomment-1"

        monkeypatch.setattr(PUBLISHER, "run", fake_run)

        assert PUBLISHER.main() == 0
        assert len(posted) == 1
        assert posted[0].splitlines()[3] == "Dovo session: `blueprint_abba01ee`"

    @pytest.mark.parametrize(
        ("comments", "expected"),
        [
            pytest.param([{"id": 7, "user": "me", "body": "<!-- dovo-review -->\nx"}], 7, id="own"),
            pytest.param([{"id": 7, "user": "me", "body": "<!-- dovo-review -->\r\nx"}], 7, id="crlf"),
            pytest.param([{"id": 7, "user": "other", "body": "<!-- dovo-review -->\nx"}], None, id="other-author"),
            pytest.param([{"id": 7, "user": "me", "body": "no marker"}], None, id="unmarked"),
            pytest.param([], None, id="none"),
        ],
    )
    def test_find_own_comment(self, comments: list[dict[str, object]], expected: int | None) -> None:
        assert PUBLISHER.find_own_comment(comments, "me") == expected

    @pytest.mark.parametrize(
        ("review", "body", "expected"),
        [
            pytest.param(_review(), "ok", None, id="match"),
            pytest.param(
                _review(head="b" * 40),
                "ok",
                f"the review covered {'b' * 40}, but the pull request head is {PR_HEAD}",
                id="mismatch",
            ),
            pytest.param(
                _review(head=None),
                "ok",
                "the review covered uncommitted changes; commit them and review again",
                id="uncommitted",
            ),
            pytest.param(
                _review(drop="head"), "ok", "review.json has no 'head'; run /dovo-review again", id="missing-head"
            ),
            pytest.param(
                _review(drop="action"), "ok", "review.json has no 'action'; run /dovo-review again", id="missing-action"
            ),
            pytest.param(
                _review(),
                "x" * 65_537,
                "the comment is 65537 characters; GitHub accepts at most 65536",
                id="too-long",
            ),
            pytest.param(
                _review(verdict="LGTM"),
                "ok",
                "review.json verdict 'LGTM' is not APPROVE or CHANGES_REQUIRED",
                id="unknown-verdict",
            ),
            pytest.param(
                _review(verdict="CHANGES_REQUIRED", action="banana", counts={"blocking": 1}),
                "ok",
                "review.json action 'banana' is not none, fix, replan or needs_human",
                id="unknown-action",
            ),
            pytest.param(
                _review(counts={"blocking": 2}),
                "ok",
                "review.json says APPROVE with 2 blocking findings",
                id="approve-with-blockers",
            ),
            pytest.param(
                _review(drop="counts"), "ok", "review.json has no 'counts'; run /dovo-review again", id="missing-counts"
            ),
            pytest.param(
                _review(round=None), "ok", "review.json round None is not a positive integer", id="round-null"
            ),
            pytest.param(_review(round=0), "ok", "review.json round 0 is not a positive integer", id="round-zero"),
            pytest.param(
                _review(counts={"blocking": False}),
                "ok",
                "review.json counts.blocking False is not a non-negative integer",
                id="blocking-bool",
            ),
            pytest.param(
                _review(counts=[]),
                "ok",
                "review.json counts.blocking None is not a non-negative integer",
                id="counts-not-object",
            ),
            pytest.param(
                _review(verdict="CHANGES_REQUIRED", action="none", counts={"blocking": 1}),
                "ok",
                "review.json action 'none' contradicts verdict CHANGES_REQUIRED",
                id="action-contradicts-verdict",
            ),
            pytest.param(
                _review(action="fix"),
                "ok",
                "review.json action 'fix' contradicts verdict APPROVE",
                id="approve-with-fix",
            ),
            pytest.param(_review(action="needs_human"), "ok", None, id="approve-needs-human"),
            pytest.param(
                _review(verdict="CHANGES_REQUIRED", action="fix", counts={"blocking": 1}),
                "ok",
                None,
                id="changes-required",
            ),
            pytest.param(
                _review(verdict="CHANGES_REQUIRED", action="needs_human", counts={"blocking": 1}),
                "ok",
                None,
                id="changes-required-needs-human",
            ),
        ],
    )
    def test_publish_problem(self, review: dict[str, object], body: str, expected: str | None) -> None:
        assert PUBLISHER.publish_problem(review, body, PR_HEAD) == expected

    @pytest.mark.parametrize(
        ("review", "report", "expected"),
        [
            pytest.param(
                _review(),
                f"**Verdict:** APPROVE · **Action:** none\n**Reviewed commit:** `{PR_HEAD}`\n",
                None,
                id="agrees",
            ),
            pytest.param(
                _review(verdict="CHANGES_REQUIRED", action="fix", counts={"blocking": 1}),
                f"**Verdict:** CHANGES REQUIRED · **Action:** fix\n**Reviewed commit:** `{PR_HEAD}`\n",
                None,
                id="changes-required-spelled-with-space",
            ),
            pytest.param(
                _review(),
                f"**Verdict:** CHANGES REQUIRED · **Action:** fix\n**Reviewed commit:** `{PR_HEAD}`\n",
                "review.md Verdict line disagrees with review.json: '**Verdict:** CHANGES REQUIRED · **Action:** fix'",
                id="stale-verdict",
            ),
            pytest.param(
                _review(),
                f"**Verdict:** APPROVE · **Action:** none\n**Reviewed commit:** `{'b' * 40}`\n",
                f"review.md Reviewed commit line disagrees with review.json: '**Reviewed commit:** `{'b' * 40}`'",
                id="stale-commit",
            ),
            pytest.param(
                _review(),
                f"**Reviewed commit:** `{PR_HEAD}`\n",
                "review.md has no Verdict line; run /dovo-review again",
                id="no-verdict-line",
            ),
        ],
    )
    def test_report_problem(self, review: dict[str, object], report: str, expected: str | None) -> None:
        assert PUBLISHER.report_problem(review, report) == expected

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            pytest.param("https://github.com/devexhq/dovo/pull/1121", ("github.com", "devexhq/dovo"), id="github"),
            pytest.param(
                "https://ghe.example.com/team/tool/pull/3/", ("ghe.example.com", "team/tool"), id="enterprise"
            ),
        ],
    )
    def test_pr_repository(self, url: str, expected: tuple[str, str]) -> None:
        assert PUBLISHER.pr_repository(url) == expected

    def test_pr_repository_rejects_non_pr_url(self) -> None:
        with pytest.raises(PUBLISHER.PublishReviewError, match="is not a pull request URL"):
            PUBLISHER.pr_repository("https://github.com/devexhq/dovo/issues/5")

    @pytest.mark.parametrize(
        ("comments", "expected"),
        [
            pytest.param(
                [{"author": {"login": "me"}, "body": f"{MARKER}\nFixed in x: y\n"}], True, id="own-same-reply"
            ),
            pytest.param([{"author": {"login": "bob"}, "body": f"{MARKER}\nFixed in x: y"}], False, id="other-author"),
            pytest.param(
                [{"author": {"login": "me"}, "body": "<!-- dovo-pr-fix round 1 -->\nFixed in x: y"}],
                False,
                id="own-other-round",
            ),
            pytest.param(
                [{"author": {"login": "me"}, "body": f"{MARKER}\nNot changed: y"}], False, id="own-other-outcome"
            ),
            pytest.param(
                [{"author": {"login": "me"}, "body": f"Quoting:\n{MARKER}\nFixed in x: y"}],
                False,
                id="marker-not-first-line",
            ),
        ],
    )
    def test_already_replied(self, comments: list[dict[str, object]], expected: bool) -> None:
        body = f"{MARKER}\nFixed in x: y"
        assert PUBLISHER.already_replied([{"author": None, "body": "x"}, *comments], "me", body) is expected

    @pytest.mark.parametrize(
        ("patch", "expected"),
        [
            pytest.param("@@ -1,2 +5,3 @@\n a\n+b\n c", (frozenset({5, 6, 7}),), id="one-hunk"),
            pytest.param(
                "@@ -1,3 +1,4 @@\n a\n+c\n+d\n e\n@@ -20,2 +30,2 @@ def f():\n x\n+y\n\\ No newline at end of file",
                (frozenset({1, 2, 3, 4}), frozenset({30, 31})),
                id="two-hunks",
            ),
            pytest.param("@@ -1,3 +1,2 @@\n a\n-b\n-c\n+d", (frozenset({1, 2}),), id="removed-lines-skipped"),
            pytest.param("@@ -1,1 +1,2 @@\n+a\u2028+b\n c", (frozenset({1, 2}),), id="line-separator-in-line"),
            pytest.param(None, (), id="no-patch"),
            pytest.param(PATCH, HUNKS["src/a.py"], id="fixture-patch"),
        ],
    )
    def test_commentable_hunks(self, patch: str | None, expected: tuple[frozenset[int], ...]) -> None:
        assert PUBLISHER.commentable_hunks(patch) == expected

    @pytest.mark.parametrize(
        ("finding", "location"),
        [
            pytest.param(_finding("W1", "WARNING", "src/a.py", 3), "src/a.py#L3-L3", id="single-line"),
            pytest.param(_finding("W1", "WARNING", "src/a.py", 5, start_line=3), "src/a.py#L3-L5", id="ranged"),
            pytest.param(_finding("W1", "WARNING", "src/b.py", None), "src/b.py", id="null-line"),
        ],
    )
    def test_agent_prompt(self, finding: dict[str, object], location: str) -> None:
        assert PUBLISHER.agent_prompt(finding) == (
            f"{PROMPT_INTRO}\nLocation: {location}\n\nPotential issue: W1 issue."
        )

    @pytest.mark.parametrize(
        ("finding", "expected"),
        [
            pytest.param(
                _finding("W1", "WARNING", "src/a.py", 5, start_line=3, also_affects=["src/b.py:1", "src/b.py:2-4"]),
                f"{PUBLISHER.REVIEW_HEADING}\n\n**Bug:** W1 summary\n**Severity:** Warning\n\n"
                f"{SUGGESTED_FIX}\n\nW1 fix.\n</details>\n\n"
                f"{PROMPT_DETAILS}\n\n```text\n{PROMPT_INTRO}\nLocation: src/a.py#L3-L5\n\n"
                "Potential issue: W1 issue.\n```\n</details>\n\n"
                "**Also affects:** `src/b.py:1`, `src/b.py:2-4`\n"
                "<!-- dovo-finding id=W1 severity=WARNING rule=R4 -->",
                id="warning-with-also-affects",
            ),
            pytest.param(
                _finding("N1", "NIT", "src/a.py", 3, rule=None),
                f"{PUBLISHER.REVIEW_HEADING}\n\n**Issue:** N1 summary\n**Severity:** Nit\n\n"
                f"{SUGGESTED_FIX}\n\nN1 fix.\n</details>\n\n"
                f"{PROMPT_DETAILS}\n\n```text\n{PROMPT_INTRO}\nLocation: src/a.py#L3-L3\n\n"
                "Potential issue: N1 issue.\n```\n</details>\n\n"
                "<!-- dovo-finding id=N1 severity=NIT rule=- -->",
                id="nit-without",
            ),
            pytest.param(
                _finding("S1", "SUGGESTION", "src/a.py", 3, issue="Quote:\n```\nx\n```"),
                f"{PUBLISHER.REVIEW_HEADING}\n\n**Issue:** S1 summary\n**Severity:** Suggestion\n\n"
                f"{SUGGESTED_FIX}\n\nS1 fix.\n</details>\n\n"
                f"{PROMPT_DETAILS}\n\n````text\n{PROMPT_INTRO}\nLocation: src/a.py#L3-L3\n\n"
                "Potential issue: Quote:\n```\nx\n```\n````\n</details>\n\n"
                "<!-- dovo-finding id=S1 severity=SUGGESTION rule=R4 -->",
                id="fenced-issue",
            ),
        ],
    )
    def test_inline_body(self, finding: dict[str, object], expected: str) -> None:
        assert PUBLISHER.inline_body(finding) == expected

    @pytest.mark.parametrize(
        ("findings", "expected"),
        [
            pytest.param([], PUBLISHER.REVIEW_HEADING, id="all-inline"),
            pytest.param(
                [
                    _finding("W1", "WARNING", "src/a.py", 40, start_line=38),
                    _finding("N1", "NIT", "docs/x.md", None, rule=None),
                ],
                f"{OUTSIDE_HEAD}(2)</b></summary><blockquote>\n\n"
                "<details>\n<summary>🟠 <b>Warning</b> · W1 summary · <code>src/a.py:38-40</code></summary>"
                "<blockquote>\n\n#### W1 · `src/a.py:38-40` · R4\n\n**Issue:** W1 issue.\n\n**Fix:** W1 fix.\n\n"
                "<!-- dovo-finding id=W1 severity=WARNING rule=R4 -->\n\n</blockquote></details>\n\n"
                "<details>\n<summary>⚪ <b>Nit</b> · N1 summary · <code>docs/x.md</code></summary>"
                "<blockquote>\n\n#### N1 · `docs/x.md`\n\n**Issue:** N1 issue.\n\n**Fix:** N1 fix.\n\n"
                "<!-- dovo-finding id=N1 severity=NIT rule=- -->\n\n</blockquote></details>\n\n"
                "</blockquote></details>",
                id="two-entries",
            ),
            pytest.param(
                [_finding("B1", "BLOCKER", None, None)],
                f"{OUTSIDE_HEAD}(1)</b></summary><blockquote>\n\n"
                "<details>\n<summary>🔴 <b>Blocker</b> · B1 summary</summary>"
                "<blockquote>\n\n#### B1 · R4\n\n**Issue:** B1 issue.\n\n**Fix:** B1 fix.\n\n"
                "<!-- dovo-finding id=B1 severity=BLOCKER rule=R4 -->\n\n</blockquote></details>\n\n"
                "</blockquote></details>",
                id="no-path",
            ),
            pytest.param(
                [_finding("S1", "SUGGESTION", "src/a.py", 40, summary="a < b && c")],
                f"{OUTSIDE_HEAD}(1)</b></summary><blockquote>\n\n"
                "<details>\n<summary>🔵 <b>Suggestion</b> · a &lt; b &amp;&amp; c · <code>src/a.py:40</code></summary>"
                "<blockquote>\n\n#### S1 · `src/a.py:40` · R4\n\n**Issue:** S1 issue.\n\n**Fix:** S1 fix.\n\n"
                "<!-- dovo-finding id=S1 severity=SUGGESTION rule=R4 -->\n\n</blockquote></details>\n\n"
                "</blockquote></details>",
                id="escaped-summary",
            ),
        ],
    )
    def test_review_body(self, findings: list[dict[str, object]], expected: str) -> None:
        assert PUBLISHER.review_body(findings) == expected

    @pytest.mark.parametrize(
        ("finding", "expected"),
        [
            pytest.param(FINDINGS[0], B1_BLOCK, id="full"),
            pytest.param(
                _finding("W2", "WARNING", "src/b.py", None),
                "#### W2 · `src/b.py` · R4\n\n**Issue:** W2 issue.\n\n**Fix:** W2 fix.\n\n"
                "<!-- dovo-finding id=W2 severity=WARNING rule=R4 -->",
                id="line-none",
            ),
            pytest.param(
                _finding("S1", "SUGGESTION", None, None),
                "#### S1 · R4\n\n**Issue:** S1 issue.\n\n**Fix:** S1 fix.\n\n"
                "<!-- dovo-finding id=S1 severity=SUGGESTION rule=R4 -->",
                id="path-none",
            ),
            pytest.param(
                _finding("N2", "NIT", "src/b.py", 7, rule=None),
                "#### N2 · `src/b.py:7`\n\n**Issue:** N2 issue.\n\n**Fix:** N2 fix.\n\n"
                "<!-- dovo-finding id=N2 severity=NIT rule=- -->",
                id="rule-none",
            ),
        ],
    )
    def test_finding_block(self, finding: dict[str, object], expected: str) -> None:
        assert PUBLISHER.finding_block(finding) == expected

    @pytest.mark.parametrize(
        ("finding", "expected_comment"),
        [
            pytest.param(
                _finding("B1", "BLOCKER", "src/a.py", 3),
                {"path": "src/a.py", "line": 3, "side": "RIGHT"},
                id="single-line",
            ),
            pytest.param(
                _finding("B1", "BLOCKER", "src/a.py", 12, start_line=10),
                {"path": "src/a.py", "start_line": 10, "start_side": "RIGHT", "line": 12, "side": "RIGHT"},
                id="multi-line",
            ),
            pytest.param(
                _finding("B1", "BLOCKER", "src/a.py", 11, start_line=3),
                {"path": "src/a.py", "line": 11, "side": "RIGHT"},
                id="hunk-crossing",
            ),
            pytest.param(_finding("B1", "BLOCKER", "src/a.py", 40), None, id="outside"),
        ],
    )
    def test_review_payload(self, finding: dict[str, object], expected_comment: dict[str, object] | None) -> None:
        payload = PUBLISHER.review_payload(_changes_required(findings=[finding]), HUNKS)

        inline = [] if expected_comment is None else [{**expected_comment, "body": PUBLISHER.inline_body(finding)}]
        outside = [finding] if expected_comment is None else []
        assert payload == {
            "commit_id": PR_HEAD,
            "event": "COMMENT",
            "body": PUBLISHER.review_body(outside),
            "comments": inline,
        }

    def test_review_payload_keeps_hunk_crossing_range_in_prompt(self) -> None:
        payload = _payload(_changes_required(findings=[_finding("B1", "BLOCKER", "src/a.py", 11, start_line=3)]))

        assert "\nLocation: src/a.py#L3-L11\n" in _inline_bodies(payload)[0]

    def test_review_payload_without_findings(self) -> None:
        assert PUBLISHER.review_payload(_review(), HUNKS) is None

    @pytest.mark.parametrize(
        ("findings", "url", "expected"),
        [
            pytest.param(
                [_finding(f"{s[0]}1", s, None, None) for s in ("BLOCKER", "WARNING", "SUGGESTION", "NIT")],
                REVIEW_URL,
                f"**Findings:** 1 blocking · 1 warning · 1 suggestion · 1 nit · [review]({REVIEW_URL})",
                id="singular-linked",
            ),
            pytest.param(
                [_finding("W1", "WARNING", None, None), _finding("W2", "WARNING", None, None)],
                None,
                "**Findings:** 0 blocking · 2 warnings · 0 suggestions · 0 nits",
                id="plural-unlinked",
            ),
            pytest.param([], None, "**Findings:** none", id="none"),
        ],
    )
    def test_findings_line(self, findings: list[dict[str, object]], url: str | None, expected: str) -> None:
        assert PUBLISHER.findings_line(findings, url) == expected

    def test_trim_report(self) -> None:
        report = (
            f"{REPORT_HEAD}### Contract coverage\n\nnone\n\n{FINDING_TABLES}"
            "### Warnings\n\nnone\n\n### Suggestions\n\nnone\n\n### Nits\n\nnone\n\n"
            "### Earlier rounds\n\n| Round | ID | Finding | Outcome |\n\n"
            "### Rejected findings\n\nnone\n\n### Residual\n\nB1, W2\n"
        )

        trimmed = PUBLISHER.trim_report(report, "**Findings:** none")

        assert trimmed == (
            f"{REPORT_HEAD}### Contract coverage\n\nnone\n\n**Findings:** none\n\n"
            "### Rejected findings\n\nnone\n\n### Residual\n\nB1, W2\n"
        )

    def test_trim_report_without_sections(self) -> None:
        assert PUBLISHER.trim_report("Report.\n", "**Findings:** none") == "Report.\n\n**Findings:** none\n"

    @pytest.mark.parametrize(
        ("review", "expected"),
        [
            pytest.param(_changes_required(), None, id="valid"),
            pytest.param(_review(drop="findings"), "review.json findings is not a list", id="not-a-list"),
            pytest.param(_review(findings=["B1"]), "review.json findings[0] is not an object", id="not-an-object"),
            pytest.param(_review(findings=[{"severity": "NIT"}]), "review.json findings[0] has no id", id="missing-id"),
            pytest.param(
                _review(findings=[{**FINDINGS[2], "id": 3}]),
                "review.json findings[0] has no id",
                id="non-string-id",
            ),
            pytest.param(
                _review(findings=[_finding("B 1", "NIT", None, None)]),
                "review.json findings[0] has no id",
                id="spaced-id",
            ),
            pytest.param(
                _review(findings=[{**FINDINGS[1], "issue": None}]),
                "review.json finding W1 has no issue",
                id="missing-issue",
            ),
            pytest.param(
                _review(findings=[{k: v for k, v in FINDINGS[1].items() if k != "summary"}]),
                "review.json finding W1 has no summary",
                id="no-summary",
            ),
            pytest.param(
                _review(findings=[_finding("W1", "WARNING", None, None, summary="")]),
                "review.json finding W1 has no summary",
                id="blank-summary",
            ),
            pytest.param(
                _review(findings=[{k: v for k, v in FINDINGS[1].items() if k != "rule"}]),
                "review.json finding W1 has no rule",
                id="no-rule",
            ),
            pytest.param(
                _review(findings=[_finding("W1", "WARNING", None, None, fix=" ")]),
                "review.json finding W1 has no fix",
                id="blank-fix",
            ),
            pytest.param(
                _review(findings=[{k: v for k, v in FINDINGS[1].items() if k != "start_line"}]),
                "review.json finding W1 has no start_line",
                id="no-start-line",
            ),
            pytest.param(
                _review(findings=[_finding("W1", "WARNING", "src/b.py", 5, start_line=5)]),
                "review.json finding W1 has start_line 5, which is not a line before its line",
                id="start-line-not-below",
            ),
            pytest.param(
                _review(findings=[_finding("W1", "WARNING", "src/b.py", 5, start_line=True)]),
                "review.json finding W1 has start_line True, which is not a line before its line",
                id="start-line-bool",
            ),
            pytest.param(
                _review(findings=[_finding("W1", "WARNING", "src/b.py", 5, start_line=0)]),
                "review.json finding W1 has start_line 0, which is not a line before its line",
                id="start-line-zero",
            ),
            pytest.param(
                _review(findings=[_finding("W1", "WARNING", "src/b.py", None, start_line=3)]),
                "review.json finding W1 has start_line 3, which is not a line before its line",
                id="start-line-without-line",
            ),
            pytest.param(
                _review(
                    findings=[
                        _finding(
                            "W1", "WARNING", "src/b.py", 5, start_line=3, also_affects=["src/b.py:1", "src/b.py:2-4"]
                        )
                    ]
                ),
                None,
                id="valid-range",
            ),
            pytest.param(
                _review(findings=[{k: v for k, v in FINDINGS[1].items() if k != "also_affects"}]),
                "review.json finding W1 has no also_affects",
                id="no-also-affects",
            ),
            pytest.param(
                _review(findings=[_finding("W1", "WARNING", None, None, also_affects="src/b.py:1")]),
                "review.json finding W1 has no also_affects",
                id="also-affects-not-list",
            ),
            pytest.param(
                _review(findings=[_finding("W1", "WARNING", None, None, also_affects=["src/b.py"])]),
                "review.json finding W1 has also_affects entry 'src/b.py', which is not path:line or path:start-end",
                id="also-affects-bad-entry",
            ),
            pytest.param(
                _review(findings=[_finding("W1", "MAJOR", None, None)]),
                "review.json finding W1 cannot be written as a finding marker",
                id="unknown-severity",
            ),
            pytest.param(
                _review(findings=[_finding("W1", "WARNING", None, None, rule="R12, R13")]),
                "review.json finding W1 cannot be written as a finding marker",
                id="spaced-rule",
            ),
        ],
    )
    def test_findings_problem(self, review: dict[str, object], expected: str | None) -> None:
        assert PUBLISHER.findings_problem(review) == (
            None if expected is None else f"{expected}; run /dovo-review again"
        )

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            pytest.param(
                "<!-- dovo-finding id=W1 severity=WARNING rule=R4 -->",
                {"id": "W1", "severity": "WARNING", "rule": "R4"},
                id="marker",
            ),
            pytest.param(
                "<!-- dovo-finding id=N1 severity=NIT rule=- -->",
                {"id": "N1", "severity": "NIT", "rule": None},
                id="no-rule",
            ),
            pytest.param("#### W1 · R4", None, id="not-a-marker"),
            pytest.param("<!-- dovo-finding id=W1 severity=WARNING rule=R12, R13 -->", None, id="spaced-rule"),
            pytest.param("<!-- dovo-finding id=W1 severity=WARNING rule=R4 round=1 -->", None, id="old-round-marker"),
        ],
    )
    def test_parse_finding_marker(self, line: str, expected: dict[str, object] | None) -> None:
        assert PUBLISHER.parse_finding_marker(line) == expected

    @pytest.mark.parametrize(
        ("reviews", "commit", "expected"),
        [
            pytest.param(
                [
                    _own_review(f"{HEADING}\nx", url=OLDER_URL, submitted="2026-10-19T10:00:00Z"),
                    _own_review(f"{HEADING}\ny", submitted="2026-10-19T11:00:00Z"),
                    _own_review(f"{HEADING}\nz", url=OLDER_URL, submitted="2026-10-19T09:00:00+00:00"),
                ],
                PR_HEAD,
                REVIEW_URL,
                id="latest-of-two",
            ),
            pytest.param(
                [
                    _own_review(f"{HEADING}\nx", url=OLDER_URL, submitted="2026-10-19T09:00:00Z"),
                    _own_review(f"{HEADING}\ny", commit="b" * 40, submitted="2026-10-19T10:00:00Z"),
                ],
                None,
                REVIEW_URL,
                id="any-commit",
            ),
            pytest.param([_own_review(f"{HEADING}\nx", commit="b" * 40)], PR_HEAD, None, id="other-commit"),
            pytest.param([_own_review("")], PR_HEAD, None, id="not-heading"),
            pytest.param([_own_review(f"{HEADING}\nx", login="bob")], PR_HEAD, None, id="other-author"),
            pytest.param([_own_review(f"{HEADING}\nx", submitted=None)], PR_HEAD, None, id="pending"),
        ],
    )
    def test_latest_review(self, reviews: list[dict[str, object]], commit: str | None, expected: str | None) -> None:
        found = PUBLISHER.latest_review(reviews, "me", commit)

        assert (found or {}).get("url") == expected

    @pytest.mark.parametrize(
        ("reviews", "expected"),
        [
            pytest.param(lambda payload: [_posted(payload)], REVIEW_URL, id="identical"),
            pytest.param(
                lambda payload: [_posted(payload, inline=tuple(reversed(_inline_bodies(payload))))],
                REVIEW_URL,
                id="inline-reordered",
            ),
            pytest.param(lambda payload: [_posted(payload, body=f"{payload['body']}\nmore")], None, id="other-body"),
            pytest.param(
                lambda payload: [_posted(payload, inline=(*_inline_bodies(payload)[:1], "other"))],
                None,
                id="other-inline-body",
            ),
            pytest.param(lambda payload: [_posted(payload, commit="b" * 40)], None, id="other-commit"),
            pytest.param(lambda payload: [_posted(payload, login="bob")], None, id="other-author"),
            pytest.param(lambda payload: [_posted(payload, submitted=None)], None, id="pending"),
            pytest.param(lambda payload: [_own_review("")], None, id="empty-thread-reply-review"),
            pytest.param(
                lambda payload: [
                    _posted(payload, url=OLDER_URL, submitted="2026-10-19T09:00:00Z"),
                    _own_review(f"{HEADING}\nother", submitted="2026-10-19T10:00:00Z"),
                ],
                None,
                id="older-identical-behind-newer",
            ),
        ],
    )
    def test_matching_review(
        self, reviews: Callable[[dict[str, object]], list[dict[str, object]]], expected: str | None
    ) -> None:
        payload = _payload(MATCH_REVIEW)

        found = PUBLISHER.matching_review(reviews(payload), "me", payload)

        assert (found or {}).get("url") == expected

    def test_main_posts_review_before_comment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        writes: list[tuple[object, ...]] = []
        review = _changes_required()
        _setup(tmp_path, monkeypatch, review, f"{REPORT_HEAD}{FINDING_TABLES}### Rejected findings\n\nnone\n", writes)

        assert PUBLISHER.main() == 0

        assert capsys.readouterr().out == (
            f"Posted pull request review: {REVIEW_URL}\nPosted review comment: {COMMENT_URL}\n"
        )
        assert writes == [
            ("review", PUBLISHER.review_payload(review, HUNKS)),
            (
                "comment",
                "POST",
                f"<!-- dovo-review -->\nReviewed commit: `{PR_HEAD}`\nVerdict: CHANGES_REQUIRED · Action: fix · Round: 1\n"
                f"\n{REPORT_HEAD}**Findings:** 1 blocking · 1 warning · 0 suggestions · 1 nit · "
                f"[review]({REVIEW_URL})\n\n### Rejected findings\n\nnone\n",
            ),
        ]
        assert TABLE_HEADER not in str(writes[1][2])

    def test_main_refuses_oversized_unlinked_comment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        writes: list[tuple[object, ...]] = []
        review = _review()
        report = f"**Verdict:** APPROVE · **Action:** none\n**Reviewed commit:** `{PR_HEAD}`\n\n{'x' * 65_500}\n"
        _setup(tmp_path, monkeypatch, review, report, writes)
        length = len(PUBLISHER.comment_body(review, PUBLISHER.trim_report(report, "**Findings:** none"), session=None))

        assert PUBLISHER.main() == 1

        assert capsys.readouterr().err == (
            f"publish_review.py: the comment is {length} characters; GitHub accepts at most 65536\n"
        )
        assert writes == []

    def test_main_reuses_identical_review(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        writes: list[tuple[object, ...]] = []
        own_comment: dict[str, object] = {"id": 5, "user": "me", "body": "<!-- dovo-review -->\nold"}
        reviews = (_posted(_payload(_changes_required()), url=OLDER_URL),)
        fake = _fake_gh(writes, comments=(own_comment,), reviews=reviews)
        _setup(tmp_path, monkeypatch, _changes_required(), f"{REPORT_HEAD}{FINDING_TABLES}", writes, fake)

        assert PUBLISHER.main() == 0

        assert capsys.readouterr().out == (
            f"Reused pull request review: {OLDER_URL}\nUpdated review comment: {COMMENT_URL}\n"
        )
        assert [write[:2] for write in writes] == [("comment", "PATCH")]
        assert f"[review]({OLDER_URL})" in str(writes[0][2])

    def test_main_posts_new_review_when_findings_differ(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        writes: list[tuple[object, ...]] = []
        reviews = (_posted(_payload(_changes_required(findings=FINDINGS[:1])), url=OLDER_URL),)
        fake = _fake_gh(writes, reviews=reviews)
        _setup(tmp_path, monkeypatch, _changes_required(), f"{REPORT_HEAD}{FINDING_TABLES}", writes, fake)

        assert PUBLISHER.main() == 0

        assert capsys.readouterr().out == (
            f"Posted pull request review: {REVIEW_URL}\nPosted review comment: {COMMENT_URL}\n"
        )
        assert writes[0] == ("review", _payload(_changes_required()))
        assert f"[review]({REVIEW_URL})" in str(writes[1][2])

    def test_main_posts_no_review_without_findings(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        writes: list[tuple[object, ...]] = []
        report = f"**Verdict:** APPROVE · **Action:** none\n**Reviewed commit:** `{PR_HEAD}`\n\n### Blocking\n\nnone\n"
        _setup(tmp_path, monkeypatch, _review(), report, writes)

        assert PUBLISHER.main() == 0

        assert [write[:2] for write in writes] == [("comment", "POST")]
        assert str(writes[0][2]).endswith(f"`{PR_HEAD}`\n\n**Findings:** none\n")

    @pytest.mark.parametrize(
        ("part", "expected"),
        [
            pytest.param("N1", "the review body is {length} characters", id="body"),
            pytest.param("B1", "the inline comment for finding B1 is {length} characters", id="inline"),
        ],
    )
    def test_main_refuses_oversized_review(
        self,
        part: str,
        expected: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        writes: list[tuple[object, ...]] = []
        findings = [{**finding, "issue": "x" * 65_536} if finding["id"] == part else finding for finding in FINDINGS]
        review = _changes_required(findings=findings)
        _setup(tmp_path, monkeypatch, review, REPORT_HEAD, writes)
        payload = PUBLISHER.review_payload(review, HUNKS)
        length = len(payload["body"] if part == "N1" else payload["comments"][0]["body"])

        assert PUBLISHER.main() == 1

        message = expected.format(length=length)
        assert capsys.readouterr().err == f"publish_review.py: {message}; GitHub accepts at most 65536\n"
        assert writes == []

    def test_main_refuses_finding_without_issue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        writes: list[tuple[object, ...]] = []
        findings = [{key: value for key, value in finding.items() if key != "issue"} for finding in FINDINGS[1:2]]
        review = _review(verdict="CHANGES_REQUIRED", action="fix", counts={"blocking": 1}, findings=findings)
        _setup(tmp_path, monkeypatch, review, REPORT_HEAD, writes)

        assert PUBLISHER.main() == 1

        assert capsys.readouterr().err == (
            "publish_review.py: review.json finding W1 has no issue; run /dovo-review again\n"
        )
        assert writes == []

    def test_main_keeps_comment_when_review_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        writes: list[tuple[object, ...]] = []
        _setup(tmp_path, monkeypatch, _changes_required(), REPORT_HEAD, writes, _fake_gh(writes, failing="review"))

        assert PUBLISHER.main() == 1

        err = capsys.readouterr().err
        assert err.startswith(
            "publish_review.py: command failed: gh api --hostname github.com -X POST repos/o/r/pulls/7/reviews --input "
        )
        assert err.endswith("HTTP 422\n\n")
        assert writes == []

    def test_main_refuses_review_json_without_start_line(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        writes: list[tuple[object, ...]] = []
        s1_finding = {key: value for key, value in FINDINGS[1].items() if key != "start_line"} | {"thread": None}
        review = _review(verdict="CHANGES_REQUIRED", action="fix", counts={"blocking": 1}, findings=[s1_finding])
        _setup(tmp_path, monkeypatch, review, REPORT_HEAD, writes)

        assert PUBLISHER.main() == 1

        assert capsys.readouterr().err == (
            "publish_review.py: review.json finding W1 has no start_line; run /dovo-review again\n"
        )
        assert writes == []


def _setup(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    review: dict[str, object],
    report: str,
    writes: list[tuple[object, ...]],
    fake_run: Callable[..., str] | None = None,
) -> None:
    """Write review.json and review.md under root, run `--pr 7` from it and answer commands through fake_run.

    fake_run defaults to _fake_gh recording into writes.
    """
    _write_review(root, review, report)
    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, "argv", ["publish_review.py", "--pr", "7"])
    monkeypatch.setattr(PUBLISHER, "run", fake_run or _fake_gh(writes))


def _fake_gh(
    writes: list[tuple[object, ...]],
    *,
    comments: tuple[dict[str, object], ...] = (),
    reviews: tuple[dict[str, object], ...] = (),
    files: tuple[dict[str, object], ...] = ({"filename": "src/a.py", "patch": PATCH},),
    failing: str | None = None,
) -> Callable[..., str]:
    """Return a `run` double answering one post to PR #7 by `me`, recording each write in writes.

    It answers `gh pr view`, the viewer, the issue comments, the `reviews` GraphQL listing and the changed
    files, and fails on any other GraphQL call, so a review thread read or mutation fails the test. A review
    POST is recorded as ("review", <payload>) and answers REVIEW_URL; a comment write as ("comment", "POST"
    or "PATCH", <body>) and answers COMMENT_URL. failing="review" makes the review POST raise
    CalledProcessError with `HTTP 422`.
    """

    listings = {"reviews": reviews, "files": files, "comments": comments}

    def fake_run(*args: str) -> str:
        if args[:3] == ("gh", "pr", "view"):
            return json.dumps({"number": 7, "headRefOid": PR_HEAD, "url": PR_URL})
        if args[-3:] == ("user", "--jq", ".login"):
            return "me"
        if "--input" in args:
            return _post_review(args, writes, failing)
        return _write_comment(args, writes) or _gh_api(args, listings)

    return fake_run


def _write_comment(args: tuple[str, ...], writes: list[tuple[object, ...]]) -> str | None:
    """Record a comment write as ("comment", "POST" or "PATCH", <body>) and answer COMMENT_URL; None for another call."""
    body_arg = next((arg for arg in args if arg.startswith("body=@")), None)
    if body_arg is None:
        return None
    body = Path(body_arg.removeprefix("body=@")).read_text(encoding="utf-8")
    writes.append(("comment", "PATCH" if "PATCH" in args else "POST", body))
    return COMMENT_URL


def _gh_api(args: tuple[str, ...], listings: dict[str, tuple[dict[str, object], ...]]) -> str:
    """Answer the `reviews` GraphQL listing, the changed files or the issue comments from listings, one JSON line per node."""
    if args[2] == "graphql":
        assert args[-1] == ".data.repository.pullRequest.reviews.nodes[]", f"ran {args}"
        return "\n".join(json.dumps(node) for node in listings["reviews"])
    listing = "files" if "repos/o/r/pulls/7/files" in args else "comments"
    assert listing == "files" or "repos/o/r/issues/7/comments" in args, f"ran {args}"
    return "\n".join(json.dumps(node) for node in listings[listing])


def _post_review(args: tuple[str, ...], writes: list[tuple[object, ...]], failing: str | None) -> str:
    """Record the review POST's --input payload in writes and answer REVIEW_URL, or fail when failing is `review`."""
    if failing == "review":
        raise subprocess.CalledProcessError(1, list(args), stderr="HTTP 422\n")
    payload = json.loads(Path(args[args.index("--input") + 1]).read_text(encoding="utf-8"))
    writes.append(("review", payload))
    return REVIEW_URL
