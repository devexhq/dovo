"""Tier 1 contract: scripts/publish_review.py stamps, finds and refuses the PR's one dovo-review comment."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.lint.astlib import REPO_ROOT

PR_HEAD = "a" * 40


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
    }
    review.update(overrides)
    if drop is not None:
        del review[drop]
    return review


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
