"""Tier 1 contract: scripts/publish_review.py stamps, finds and refuses the PR's one dovo-review comment."""

from __future__ import annotations

import importlib.util
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


def _review(head: str | None = PR_HEAD, drop: str | None = None) -> dict[str, object]:
    """Return a stamped review.json payload with head, minus the drop key when given."""
    review: dict[str, object] = {"head": head, "verdict": "APPROVE", "action": "none", "round": 1}
    if drop is not None:
        del review[drop]
    return review


class PublishReviewTests:
    def test_comment_body_stamps_header(self) -> None:
        review = {"head": PR_HEAD, "verdict": "APPROVE", "action": "none", "round": 2}

        body = PUBLISHER.comment_body(review, "## dovo-review - round 2\n")

        assert body == (
            "<!-- dovo-review -->\n"
            f"Reviewed commit: `{PR_HEAD}`\n"
            "Verdict: APPROVE · Action: none · Round: 2\n"
            "\n"
            "## dovo-review - round 2\n"
        )

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
        ],
    )
    def test_publish_problem(self, review: dict[str, object], body: str, expected: str | None) -> None:
        assert PUBLISHER.publish_problem(review, body, PR_HEAD) == expected
