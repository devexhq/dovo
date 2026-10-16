"""Tier 1 unit tests for FormatterCase harness and assertion helpers."""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from rich.panel import Panel

from dovo.common.types import ComponentFormatter
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
    render_rich,
)


class DummyData(BaseModel):
    model_config = {"extra": "forbid", "strict": True}

    message: str
    count: int


class DummyView(BaseModel):
    model_config = {"extra": "forbid", "strict": True}

    message: str
    count: int


class DummyFormatter(ComponentFormatter[DummyData, DummyView]):
    def transform(self, data: DummyData) -> DummyView:
        return DummyView(message=data.message, count=data.count)

    def to_rich(self, data: DummyData | DummyView) -> Panel:
        return Panel(f"{data.message}: {data.count}")


class DummyMissingFormatter(ComponentFormatter[DummyData, DummyView]):
    def transform(self, data: DummyData) -> DummyView:
        return DummyView(message=data.message, count=data.count)

    def to_rich(self, data: DummyData | DummyView) -> Panel:
        return Panel("Incomplete content")


class FormatterHarnessTests:
    """Tier 1 unit verification for formatter harness helpers."""

    def test_formatter_case_initialization_with_defaults(self) -> None:
        data = DummyData(message="hello", count=1)
        view = DummyView(message="hello", count=1)
        case = FormatterCase(data=data, view=view)

        assert case.data == data
        assert case.view == view
        assert case.render_expectations == []

    def test_render_rich_renders_at_pinned_width_160(self) -> None:
        panel = Panel("test content", width=160)
        output = render_rich(panel)

        assert "test content" in output

    def test_assert_rich_render_shows_every_view_value_passes_when_present(self) -> None:
        view = DummyView(message="operational", count=42)

        assert_rich_render_shows_every_view_value(DummyFormatter, view, ["operational", "42"])

    def test_assert_rich_render_shows_every_view_value_fails_when_field_missing(self) -> None:
        view = DummyView(message="operational", count=42)

        with pytest.raises(AssertionError, match="missing from Rich renderable output"):
            assert_rich_render_shows_every_view_value(DummyMissingFormatter, view, ["operational", "42"])

    def test_assert_rich_render_shows_every_view_value_supports_formatter_case(self) -> None:
        data = DummyData(message="operational", count=42)
        view = DummyView(message="operational", count=42)
        case = FormatterCase(data=data, view=view, render_expectations=["operational", "42"])

        assert_rich_render_shows_every_view_value(DummyFormatter, case, case.render_expectations)

    def test_assert_transform_derives_expected_view_passes_on_match(self) -> None:
        data = DummyData(message="hello", count=1)
        expected = DummyView(message="hello", count=1)

        assert_transform_derives_expected_view(DummyFormatter, data, expected)

    def test_assert_transform_derives_expected_view_fails_on_mismatch(self) -> None:
        data = DummyData(message="hello", count=1)
        mismatched = DummyView(message="different", count=99)

        with pytest.raises(AssertionError):
            assert_transform_derives_expected_view(DummyFormatter, data, mismatched)

    def test_assert_json_payload_matches_published_shape_passes_on_exact_match(self) -> None:
        view = DummyView(message="hello", count=1)
        expected = {"message": "hello", "count": 1}

        assert_json_payload_matches_published_shape(DummyFormatter, view, expected)

    def test_assert_json_payload_matches_published_shape_fails_on_wire_mismatch(self) -> None:
        view = DummyView(message="hello", count=1)
        wrong_payload = {"message": "hello", "count": 99, "extra": True}

        with pytest.raises(AssertionError, match="Wire format mismatch"):
            assert_json_payload_matches_published_shape(DummyFormatter, view, wrong_payload)

    def test_assert_json_payload_matches_published_shape_supports_formatter_case_and_domain_data(self) -> None:
        data = DummyData(message="hello", count=1)
        view = DummyView(message="hello", count=1)
        case = FormatterCase(data=data, view=view)
        expected = {"message": "hello", "count": 1}

        assert_json_payload_matches_published_shape(DummyFormatter, case, expected)
        assert_json_payload_matches_published_shape(DummyFormatter, data, expected)


class RenderExpectationGuardTests:
    """Unit tests for the empty-expectation guard in assert_rich_render_shows_every_view_value."""

    def test_empty_expectations_raise(self) -> None:
        """Raise AssertionError naming the missing expectations when the case lists none."""
        case = FormatterCase(
            data=DummyData(message="operational", count=42),
            view=DummyView(message="operational", count=42),
            render_expectations=[],
        )

        with pytest.raises(AssertionError, match="no render expectations"):
            assert_rich_render_shows_every_view_value(DummyFormatter, case.data, case.render_expectations)
