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
REVIEW_MARKER = f"<!-- dovo-review-findings head={PR_HEAD} round=1 -->"


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
        "line": line,
        "rule": rule,
        "summary": f"{finding_id} summary",
        "issue": f"{finding_id} issue.",
        "fix": f"{finding_id} fix.",
        "thread": None,
    }
    finding.update(overrides)
    return finding


FINDINGS = [
    _finding("B1", "BLOCKER", "src/a.py", 3),
    _finding("W1", "WARNING", "src/a.py", 40),
    _finding("N1", "NIT", None, None, rule=None),
]
DIFF_LINES = {"src/a.py": frozenset({3, 4})}
PATCH = "@@ -1,2 +3,2 @@\n line\n+added"
B1_BLOCK = "<!-- dovo-finding id=B1 severity=BLOCKER rule=R4 round=1 -->\n#### B1 · `src/a.py:3` · R4\n\n**Issue:** B1 issue.\n\n**Fix:** B1 fix."
W1_BLOCK = "<!-- dovo-finding id=W1 severity=WARNING rule=R4 round=1 -->\n#### W1 · `src/a.py:40` · R4\n\n**Issue:** W1 issue.\n\n**Fix:** W1 fix."
N1_BLOCK = (
    "<!-- dovo-finding id=N1 severity=NIT rule=- round=1 -->\n#### N1\n\n**Issue:** N1 issue.\n\n**Fix:** N1 fix."
)
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


def _own_review(body: str, login: str = "me") -> dict[str, object]:
    """Return a REVIEWS_QUERY node by login with body and REVIEW_URL."""
    return {"author": {"__typename": "User", "login": login}, "body": body, "url": REVIEW_URL}


def _thread_node(thread_id: str, comments: list[tuple[str, str]], *, resolved: bool = False) -> dict[str, object]:
    """Return a THREADS_QUERY node whose comments are (login, body) pairs, in order."""
    nodes = [{"author": {"__typename": "User", "login": login}, "body": body} for login, body in comments]
    return {"id": thread_id, "isResolved": resolved, "path": "src/a.py", "line": 3, "comments": {"nodes": nodes}}


ROUND_1_B1 = "<!-- dovo-finding id=B1 severity=BLOCKER rule=R4 round=1 -->\n#### B1\n\n**Issue:** x\n\n**Fix:** y"
ROUND_1_W1 = "<!-- dovo-finding id=W1 severity=WARNING rule=R4 round=1 -->\n#### W1\n\n**Issue:** x\n\n**Fix:** y"
CONTINUATION = "<!-- dovo-finding id=B1 severity=BLOCKER rule=R4 round=2 -->\nStill open in round 2 as B1."
ROUND_2_REPORT = REPORT_HEAD + FINDING_TABLES


def _round_2(*findings: dict[str, object]) -> dict[str, object]:
    """Return a round-2 CHANGES_REQUIRED review.json payload with findings."""
    return _changes_required(round=2, findings=list(findings))


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
            pytest.param(
                "@@ -1,3 +1,4 @@\n a\n-b\n+c\n+d\n e\n@@ -20,2 +30,2 @@ def f():\n x\n+y\n\\ No newline at end of file",
                frozenset({1, 2, 3, 4, 30, 31}),
                id="two-hunks",
            ),
            pytest.param(None, frozenset(), id="no-patch"),
        ],
    )
    def test_commentable_lines(self, patch: str | None, expected: frozenset[int]) -> None:
        assert PUBLISHER.commentable_lines(patch) == expected

    @pytest.mark.parametrize(
        ("finding", "expected"),
        [
            pytest.param(FINDINGS[0], B1_BLOCK, id="full"),
            pytest.param(
                _finding("W2", "WARNING", "src/b.py", None),
                "<!-- dovo-finding id=W2 severity=WARNING rule=R4 round=1 -->\n#### W2 · `src/b.py` · R4\n\n"
                "**Issue:** W2 issue.\n\n**Fix:** W2 fix.",
                id="line-none",
            ),
            pytest.param(
                _finding("S1", "SUGGESTION", None, None),
                "<!-- dovo-finding id=S1 severity=SUGGESTION rule=R4 round=1 -->\n#### S1 · R4\n\n"
                "**Issue:** S1 issue.\n\n**Fix:** S1 fix.",
                id="path-none",
            ),
            pytest.param(
                _finding("N2", "NIT", "src/b.py", 7, rule=None),
                "<!-- dovo-finding id=N2 severity=NIT rule=- round=1 -->\n#### N2 · `src/b.py:7`\n\n"
                "**Issue:** N2 issue.\n\n**Fix:** N2 fix.",
                id="rule-none",
            ),
        ],
    )
    def test_finding_block(self, finding: dict[str, object], expected: str) -> None:
        assert PUBLISHER.finding_block(finding, 1) == expected

    def test_review_payload(self) -> None:
        payload = PUBLISHER.review_payload(_changes_required(), DIFF_LINES)

        assert payload == {
            "commit_id": PR_HEAD,
            "event": "COMMENT",
            "body": f"{REVIEW_MARKER}\n**Findings posted: 3** (1 inline, 2 below)\n\n{W1_BLOCK}\n\n{N1_BLOCK}",
            "comments": [{"path": "src/a.py", "line": 3, "side": "RIGHT", "body": B1_BLOCK}],
        }

    def test_review_payload_without_findings(self) -> None:
        assert PUBLISHER.review_payload(_review(), DIFF_LINES) is None

    @pytest.mark.parametrize(
        ("findings", "url", "expected"),
        [
            pytest.param(
                [_finding(f"{s[0]}1", s, None, None) for s in ("BLOCKER", "WARNING", "SUGGESTION", "NIT")],
                REVIEW_URL,
                f"**Findings:** 1 blocking · 1 warning · 1 suggestion · 1 nit · [round 2 review]({REVIEW_URL})",
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
        assert PUBLISHER.findings_line(findings, 2, url) == expected

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
                _review(findings=[_finding("W1", "WARNING", None, None, fix=" ")]),
                "review.json finding W1 has no fix",
                id="blank-fix",
            ),
            pytest.param(
                _review(findings=[{k: v for k, v in FINDINGS[1].items() if k != "thread"}]),
                "review.json finding W1 has no thread",
                id="missing-thread",
            ),
            pytest.param(
                _review(findings=[_finding("W1", "WARNING", None, None, thread="")]),
                "review.json finding W1 has no thread",
                id="empty-thread",
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
                "<!-- dovo-finding id=W1 severity=WARNING rule=R4 round=2 -->",
                {"id": "W1", "severity": "WARNING", "rule": "R4", "round": 2},
                id="marker",
            ),
            pytest.param(
                "<!-- dovo-finding id=N1 severity=NIT rule=- round=1 -->",
                {"id": "N1", "severity": "NIT", "rule": None, "round": 1},
                id="no-rule",
            ),
            pytest.param("#### W1 · R4", None, id="not-a-marker"),
            pytest.param("<!-- dovo-finding id=W1 severity=WARNING rule=R12, R13 round=2 -->", None, id="spaced-rule"),
        ],
    )
    def test_parse_finding_marker(self, line: str, expected: dict[str, object] | None) -> None:
        assert PUBLISHER.parse_finding_marker(line) == expected

    @pytest.mark.parametrize(
        ("reviews", "expected"),
        [
            pytest.param([_own_review(f"{REVIEW_MARKER}\nx")], REVIEW_URL, id="own"),
            pytest.param([_own_review(f"{REVIEW_MARKER}\nx", login="bob")], None, id="other-author"),
            pytest.param([_own_review(REVIEW_MARKER.replace("round=1", "round=2"))], None, id="other-round"),
            pytest.param([_own_review(f"Quoting:\n{REVIEW_MARKER}")], None, id="marker-not-first-line"),
        ],
    )
    def test_own_review(self, reviews: list[dict[str, object]], expected: str | None) -> None:
        found = PUBLISHER.own_review(reviews, "me", REVIEW_MARKER)

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
            ("review", PUBLISHER.review_payload(review, DIFF_LINES)),
            (
                "comment",
                "POST",
                f"<!-- dovo-review -->\nReviewed commit: `{PR_HEAD}`\nVerdict: CHANGES_REQUIRED · Action: fix · Round: 1\n"
                f"\n{REPORT_HEAD}**Findings:** 1 blocking · 1 warning · 0 suggestions · 1 nit · "
                f"[round 1 review]({REVIEW_URL})\n\n### Rejected findings\n\nnone\n",
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

    def test_main_reuses_round_review(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        writes: list[tuple[object, ...]] = []
        own_comment: dict[str, object] = {"id": 5, "user": "me", "body": "<!-- dovo-review -->\nold"}
        reviews = (_own_review(f"{REVIEW_MARKER}\n**Findings posted: 3**"),)
        fake = _fake_gh(writes, comments=(own_comment,), reviews=reviews)
        _setup(tmp_path, monkeypatch, _changes_required(), f"{REPORT_HEAD}{FINDING_TABLES}", writes, fake)

        assert PUBLISHER.main() == 0

        assert capsys.readouterr().out == (
            f"Reused pull request review: {REVIEW_URL}\nUpdated review comment: {COMMENT_URL}\n"
        )
        assert [write[:2] for write in writes] == [("comment", "PATCH")]
        assert f"[round 1 review]({REVIEW_URL})" in str(writes[0][2])

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
        payload = PUBLISHER.review_payload(review, DIFF_LINES)
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

    def test_continuation_body(self) -> None:
        finding = _finding("W3", "WARNING", "src/a.py", 46)

        assert PUBLISHER.continuation_body(finding, 2) == (
            "<!-- dovo-finding id=W3 severity=WARNING rule=R4 round=2 -->\nStill open in round 2 as W3."
        )

    @pytest.mark.parametrize(
        ("thread", "expected"),
        [
            pytest.param("PRRT_D", None, id="viewer-finding-thread"),
            pytest.param("PRRT_X", None, id="not-listed"),
            pytest.param(
                "PRRT_F",
                "review.json finding B1 names thread PRRT_F, which is not a dovo finding thread on pull request #7",
                id="other-author",
            ),
            pytest.param(
                "PRRT_P",
                "review.json finding B1 names thread PRRT_P, which is not a dovo finding thread on pull request #7",
                id="no-marker",
            ),
        ],
    )
    def test_thread_problem(self, thread: str, expected: str | None) -> None:
        threads = [
            _thread_node("PRRT_D", [("me", ROUND_1_B1)]),
            _thread_node("PRRT_F", [("bob", ROUND_1_B1)]),
            _thread_node("PRRT_P", [("me", "Plain note.")]),
        ]
        review = _round_2(_finding("B1", "BLOCKER", "src/a.py", 3, thread=thread))

        problem = PUBLISHER.thread_problem(review, threads, PUBLISHER.finding_threads(threads, "me"), 7)

        assert problem == expected

    def test_detach_unlisted(self) -> None:
        review = _round_2(
            _finding("B1", "BLOCKER", "src/a.py", 3, thread="PRRT_D"),
            _finding("W1", "WARNING", "src/a.py", 4, thread="PRRT_GONE"),
            _finding("N1", "NIT", None, None),
        )

        detached_review, detached = PUBLISHER.detach_unlisted(review, [_thread_node("PRRT_D", [("me", ROUND_1_B1)])])

        assert [finding["thread"] for finding in detached_review["findings"]] == ["PRRT_D", None, None]
        assert detached == [("W1", "PRRT_GONE")]

    def test_threads_to_resolve(self) -> None:
        threads = [
            _thread_node("PRRT_OPEN", [("me", ROUND_1_W1)]),
            _thread_node("PRRT_CONTINUED", [("me", ROUND_1_B1)], resolved=True),
            _thread_node("PRRT_DONE", [("me", ROUND_1_W1)], resolved=True),
            _thread_node("PRRT_NEW", [("me", CONTINUATION)]),
        ]
        review = _round_2(_finding("B1", "BLOCKER", "src/a.py", 3, thread="PRRT_CONTINUED"))

        assert PUBLISHER.threads_to_resolve(review, PUBLISHER.finding_threads(threads, "me")) == ["PRRT_OPEN"]

    def test_review_payload_skips_continued(self) -> None:
        continued = _finding("B1", "BLOCKER", "src/a.py", 3, thread="PRRT_D")
        n1 = {**FINDINGS[2], "id": "N2"}

        payload = PUBLISHER.review_payload(_round_2(continued, n1), DIFF_LINES)

        assert payload is not None
        assert payload["comments"] == []
        assert str(payload["body"]).splitlines()[1] == "**Findings posted: 2** (1 inline, 1 below)"
        assert PUBLISHER.review_payload(_round_2(continued), DIFF_LINES) is None

    def test_main_continues_and_resolves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        writes: list[tuple[object, ...]] = []
        threads = (
            _thread_node("PRRT_A", [("me", ROUND_1_B1)], resolved=True),
            _thread_node("PRRT_B", [("me", ROUND_1_W1)]),
            _thread_node("PRRT_C", [("alice", "Rename it.")]),
        )
        review = _round_2(_finding("B1", "BLOCKER", "src/a.py", 3, thread="PRRT_A"), FINDINGS[2])
        _setup(tmp_path, monkeypatch, review, ROUND_2_REPORT, writes, _fake_gh(writes, threads=threads))

        assert PUBLISHER.main() == 0

        assert capsys.readouterr().out == (
            f"Posted pull request review: {REVIEW_URL}\nContinued 1 finding threads and resolved 1\n"
            f"Posted review comment: {COMMENT_URL}\n"
        )
        assert [write[:2] for write in writes] == [
            ("review", PUBLISHER.review_payload(review, DIFF_LINES)),
            ("reply", "PRRT_A"),
            ("unresolve", "PRRT_A"),
            ("resolve", "PRRT_B"),
            ("comment", "POST"),
        ]
        assert writes[1] == ("reply", "PRRT_A", CONTINUATION)

    def test_main_continues_once_on_retry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        writes: list[tuple[object, ...]] = []
        threads = (
            _thread_node("PRRT_A", [("me", ROUND_1_B1), ("me", CONTINUATION)]),
            _thread_node("PRRT_B", [("me", ROUND_1_W1)], resolved=True),
        )
        reviews = (_own_review(REVIEW_MARKER.replace("round=1", "round=2")),)
        own_comment: dict[str, object] = {"id": 5, "user": "me", "body": "<!-- dovo-review -->\nold"}
        review = _round_2(_finding("B1", "BLOCKER", "src/a.py", 3, thread="PRRT_A"), FINDINGS[2])
        fake = _fake_gh(writes, comments=(own_comment,), reviews=reviews, threads=threads)
        _setup(tmp_path, monkeypatch, review, ROUND_2_REPORT, writes, fake)

        assert PUBLISHER.main() == 0

        assert capsys.readouterr().out == (
            f"Reused pull request review: {REVIEW_URL}\nUpdated review comment: {COMMENT_URL}\n"
        )
        assert [write[:2] for write in writes] == [("comment", "PATCH")]

    def test_main_posts_no_review_when_all_continued(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        writes: list[tuple[object, ...]] = []
        threads = (
            _thread_node("PRRT_A", [("me", ROUND_1_B1)]),
            _thread_node("PRRT_B", [("me", ROUND_1_W1)]),
        )
        review = _round_2(_finding("B1", "BLOCKER", "src/a.py", 3, thread="PRRT_A"))
        _setup(tmp_path, monkeypatch, review, ROUND_2_REPORT, writes, _fake_gh(writes, threads=threads))

        assert PUBLISHER.main() == 0

        assert [write[:2] for write in writes] == [("reply", "PRRT_A"), ("resolve", "PRRT_B"), ("comment", "POST")]
        assert str(writes[2][2]).endswith(
            f"`{PR_HEAD}`\n\nOne blocker.\n\n**Findings:** 1 blocking · 0 warnings · 0 suggestions · 0 nits\n"
        )

    def test_main_refuses_foreign_thread(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        writes: list[tuple[object, ...]] = []
        threads = (_thread_node("PRRT_C", [("alice", "Rename it.")]),)
        review = _round_2(_finding("B1", "BLOCKER", "src/a.py", 3, thread="PRRT_C"))
        _setup(tmp_path, monkeypatch, review, ROUND_2_REPORT, writes, _fake_gh(writes, threads=threads))

        assert PUBLISHER.main() == 1

        assert capsys.readouterr().err == (
            "publish_review.py: review.json finding B1 names thread PRRT_C, "
            "which is not a dovo finding thread on pull request #7\n"
        )
        assert writes == []

    def test_main_posts_unlisted_thread_as_new(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        writes: list[tuple[object, ...]] = []
        review = _round_2(_finding("B1", "BLOCKER", "src/a.py", 3, thread="PRRT_GONE"))
        _setup(tmp_path, monkeypatch, review, ROUND_2_REPORT, writes)

        assert PUBLISHER.main() == 0

        assert capsys.readouterr().out == (
            f"Posted pull request review: {REVIEW_URL}\n"
            "Posted finding B1 as new: thread PRRT_GONE is not on pull request #7\n"
            f"Posted review comment: {COMMENT_URL}\n"
        )
        assert [write[0] for write in writes] == ["review", "comment"]
        payload = writes[0][1]
        assert isinstance(payload, dict)
        assert [comment["path"] for comment in payload["comments"]] == ["src/a.py"]


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
    threads: tuple[dict[str, object], ...] = (),
    files: tuple[dict[str, object], ...] = ({"filename": "src/a.py", "patch": PATCH},),
    failing: str | None = None,
) -> Callable[..., str]:
    """Return a `run` double answering one post to PR #7 by `me`, recording each write in writes.

    It answers `gh pr view`, the viewer, the issue comments, the `reviews` and `reviewThreads` GraphQL
    listings and the changed files. A review POST is recorded as ("review", <payload>) and answers REVIEW_URL;
    a comment write as ("comment", "POST" or "PATCH", <body>) and answers COMMENT_URL; a thread mutation as
    ("reply", thread, body), ("unresolve", thread) or ("resolve", thread). failing="review" makes the review
    POST raise CalledProcessError with `HTTP 422`.
    """

    listings = {"reviews": reviews, "reviewThreads": threads, "files": files, "comments": comments}

    def fake_run(*args: str) -> str:
        if args[:3] == ("gh", "pr", "view"):
            return json.dumps({"number": 7, "headRefOid": PR_HEAD, "url": PR_URL})
        if args[-3:] == ("user", "--jq", ".login"):
            return "me"
        if "--input" in args:
            return _post_review(args, writes, failing)
        return _write_comment(args, writes) or _gh_api(args, writes, listings)

    return fake_run


def _write_comment(args: tuple[str, ...], writes: list[tuple[object, ...]]) -> str | None:
    """Record a comment write as ("comment", "POST" or "PATCH", <body>) and answer COMMENT_URL; None for another call."""
    body_arg = next((arg for arg in args if arg.startswith("body=@")), None)
    if body_arg is None:
        return None
    body = Path(body_arg.removeprefix("body=@")).read_text(encoding="utf-8")
    writes.append(("comment", "PATCH" if "PATCH" in args else "POST", body))
    return COMMENT_URL


def _gh_api(
    args: tuple[str, ...], writes: list[tuple[object, ...]], listings: dict[str, tuple[dict[str, object], ...]]
) -> str:
    """Answer a GraphQL call, the changed files or the issue comments from listings, one JSON line per node."""
    if args[2] == "graphql":
        return _graphql(args, writes, listings)
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


def _graphql(
    args: tuple[str, ...], writes: list[tuple[object, ...]], listings: dict[str, tuple[dict[str, object], ...]]
) -> str:
    """Answer a `gh api graphql` call: a pull request listing from listings, or a thread mutation recorded in writes."""
    connection = args[-1].removeprefix(".data.repository.pullRequest.").removesuffix(".nodes[]")
    if connection in listings:
        return "\n".join(json.dumps(node) for node in listings[connection])
    fields = [args[index + 1] for index, arg in enumerate(args) if arg == "-f"]
    calls = {
        f"query={PUBLISHER.REPLY_MUTATION}": "reply",
        f"query={PUBLISHER.UNRESOLVE_MUTATION}": "unresolve",
        f"query={PUBLISHER.RESOLVE_MUTATION}": "resolve",
    }
    writes.append((calls[fields[0]], fields[1].removeprefix("thread="), *(f.removeprefix("body=") for f in fields[2:])))
    return ""
