"""Tier 2 presentation contract tests for ArtifactsPruneFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.formatters.artifacts.artifacts_prune import ArtifactsPruneFormatter
from dovo.cli.ui.formatters.artifacts.artifacts_views import ArtifactsPruneView
from dovo.core.artifacts.models import ArtifactsPruneResult, ArtifactsPruneStatus, PrunedArtifact
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
)

WITH_PRUNED_ITEM = FormatterCase(
    data=ArtifactsPruneResult(
        status=ArtifactsPruneStatus.OK,
        dry_run=False,
        force=False,
        items=[PrunedArtifact(session_id="wf_abc123", name="dist-packages", pruned=True)],
    ),
    view=ArtifactsPruneView(
        status=ArtifactsPruneStatus.OK,
        dry_run=False,
        force=False,
        items=[PrunedArtifact(session_id="wf_abc123", name="dist-packages", pruned=True)],
        pruned_count=1,
        failed_count=0,
    ),
    render_expectations=["wf_abc123", "dist-packages"],
)

DISABLED = FormatterCase(
    data=ArtifactsPruneResult(status=ArtifactsPruneStatus.DISABLED, dry_run=False, force=False, items=[]),
    view=ArtifactsPruneView(
        status=ArtifactsPruneStatus.DISABLED, dry_run=False, force=False, items=[], pruned_count=0, failed_count=0
    ),
    render_expectations=["disabled"],
)

ARTIFACTS_PRUNE_PAYLOAD_CASES = [
    pytest.param(
        WITH_PRUNED_ITEM,
        {
            "status": "ok",
            "dry_run": False,
            "force": False,
            "items": [{"session_id": "wf_abc123", "name": "dist-packages", "pruned": True, "error": None}],
            "pruned_count": 1,
            "failed_count": 0,
        },
        id="with_pruned_item",
    ),
    pytest.param(
        DISABLED,
        {"status": "disabled", "dry_run": False, "force": False, "items": [], "pruned_count": 0, "failed_count": 0},
        id="disabled",
    ),
]


class ArtifactsPruneFormatterTests:
    """Tier 2 presentation contract tests for ArtifactsPruneFormatter."""

    def test_transform_derives_pruned_and_failed_counts(self) -> None:
        """[tier-2/unit] ArtifactsPruneFormatter.transform: items list derives pruned_count/failed_count aggregates."""
        assert_transform_derives_expected_view(ArtifactsPruneFormatter, WITH_PRUNED_ITEM.data, WITH_PRUNED_ITEM.view)

    @pytest.mark.parametrize(("case", "expected_payload"), ARTIFACTS_PRUNE_PAYLOAD_CASES)
    def test_to_json_serializable_matches_literal_populated_and_empty_dicts(
        self, case: FormatterCase[ArtifactsPruneResult, ArtifactsPruneView], expected_payload: dict[str, Any]
    ) -> None:
        """Verify to_json_serializable matches the published wire format dict for a populated and an empty/disabled state."""
        assert_json_payload_matches_published_shape(ArtifactsPruneFormatter, case, expected_payload)

    def test_to_rich_renders_disabled_state_without_items(self) -> None:
        """[tier-2/unit] ArtifactsPruneFormatter.to_rich: DISABLED status renders without any pruned item lines."""
        assert_rich_render_shows_every_view_value(ArtifactsPruneFormatter, DISABLED.data, DISABLED.render_expectations)

    def test_to_rich_renders_pinned_session_id_and_name(self) -> None:
        """[tier-2/unit] ArtifactsPruneFormatter.to_rich: render_rich output contains the pruned item's session_id and name."""
        assert_rich_render_shows_every_view_value(
            ArtifactsPruneFormatter, WITH_PRUNED_ITEM.data, WITH_PRUNED_ITEM.render_expectations
        )
