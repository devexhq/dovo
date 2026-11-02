"""Contract tests for splitting a condition's `artifacts.<name>.<field>[.<path>]` segments."""

from __future__ import annotations

import pytest

from dovo.core.inputs import ArtifactReference, artifact_reference


class ArtifactReferenceTests:
    @pytest.mark.parametrize(
        ("segments", "expected"),
        [
            pytest.param(("plan", "content"), ArtifactReference("plan", "content"), id="name_and_field"),
            pytest.param(("team", "plan", "revision"), ArtifactReference("team.plan", "revision"), id="dotted_name"),
            pytest.param(
                ("verdict", "content", "done"),
                ArtifactReference("verdict", "content", ("done",)),
                id="content_path",
            ),
            pytest.param(("notes.dir", "content"), ArtifactReference("notes.dir", "content"), id="bracket_key_name"),
            pytest.param(("content", "content"), ArtifactReference("content", "content"), id="field_word_as_name"),
            pytest.param(("dir", "digest"), ArtifactReference("dir", "digest"), id="dir_named_artifact"),
        ],
    )
    def test_reference_splits_name_field_and_content_path(
        self, segments: tuple[str | int, ...], expected: ArtifactReference
    ) -> None:
        assert artifact_reference(segments) == expected

    @pytest.mark.parametrize(
        "segments",
        [
            pytest.param((), id="no_segment"),
            pytest.param(("plan",), id="name_only"),
            pytest.param(("plan", "bogus"), id="unknown_field"),
            pytest.param((0, "content"), id="int_name_segment"),
        ],
    )
    def test_segments_without_a_field_are_no_reference(self, segments: tuple[str | int, ...]) -> None:
        assert artifact_reference(segments) is None
