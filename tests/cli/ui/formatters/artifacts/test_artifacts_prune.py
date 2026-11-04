"""Tier 2 presentation contract tests for ArtifactsPruneFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.formatters.artifacts.artifacts_prune import ArtifactsPruneFormatter
from dovo.cli.ui.formatters.artifacts.artifacts_views import ArtifactsPruneView
from dovo.core.artifacts.models import ArtifactsPruneResult, PrunedArtifact
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
)

WITH_PRUNED_ITEM = FormatterCase(
    data=ArtifactsPruneResult(
        dry_run=False,
        force=False,
        items=[PrunedArtifact(session_id="wf_abc123", name="dist-packages", revision=2, pruned=True)],
    ),
    view=ArtifactsPruneView(
        ok=True,
        disabled=False,
        dry_run=False,
        force=False,
        items=[PrunedArtifact(session_id="wf_abc123", name="dist-packages", revision=2, pruned=True)],
        pruned_count=1,
        failed_count=0,
    ),
    render_expectations=["wf_abc123", "dist-packages", "@2"],
)

DISABLED = FormatterCase(
    data=ArtifactsPruneResult(disabled=True, dry_run=False, force=False, items=[]),
    view=ArtifactsPruneView(
        ok=True, disabled=True, dry_run=False, force=False, items=[], pruned_count=0, failed_count=0
    ),
    render_expectations=["Artifact pruning is disabled; pass --force to override."],
)

ARTIFACTS_PRUNE_PAYLOAD_CASES = [
    pytest.param(
        WITH_PRUNED_ITEM,
        {
            "ok": True,
            "error_code": None,
            "errors": [],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
            "disabled": False,
            "dry_run": False,
            "force": False,
            "items": [
                {"session_id": "wf_abc123", "name": "dist-packages", "revision": 2, "pruned": True, "error": None}
            ],
            "pruned_count": 1,
            "failed_count": 0,
        },
        id="with_pruned_item",
    ),
    pytest.param(
        DISABLED,
        {
            "ok": True,
            "error_code": None,
            "errors": [],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
            "disabled": True,
            "dry_run": False,
            "force": False,
            "items": [],
            "pruned_count": 0,
            "failed_count": 0,
        },
        id="disabled",
    ),
]


class ArtifactsPruneFormatterTests:
    """Tier 2 presentation contract tests for ArtifactsPruneFormatter."""

    def test_transform_derives_pruned_and_failed_counts(self) -> None:
        assert_transform_derives_expected_view(ArtifactsPruneFormatter, WITH_PRUNED_ITEM.data, WITH_PRUNED_ITEM.view)

    @pytest.mark.parametrize(("case", "expected_payload"), ARTIFACTS_PRUNE_PAYLOAD_CASES)
    def test_to_json_serializable_matches_literal_populated_and_empty_dicts(
        self, case: FormatterCase[ArtifactsPruneResult, ArtifactsPruneView], expected_payload: dict[str, Any]
    ) -> None:
        assert_json_payload_matches_published_shape(ArtifactsPruneFormatter, case, expected_payload)

    def test_to_rich_renders_disabled_state_without_items(self) -> None:
        assert_rich_render_shows_every_view_value(ArtifactsPruneFormatter, DISABLED.data, DISABLED.render_expectations)

    def test_to_rich_renders_pinned_session_id_and_name(self) -> None:
        """Show the pruned item's session_id, name and revision in the render."""
        assert_rich_render_shows_every_view_value(
            ArtifactsPruneFormatter, WITH_PRUNED_ITEM.data, WITH_PRUNED_ITEM.render_expectations
        )
