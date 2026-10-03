"""Tier 1 unit tests for FormatterCase harness and assertion helpers."""

from __future__ import annotations

from pathlib import Path

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


class ComplexData(BaseModel):
    model_config = {"extra": "forbid", "strict": True}

    item_path: Path
    file_path: str
    score: float
    is_active: bool
    border_style: str
    style: str
    color: str
    tags: list[str]
    coordinates: tuple[float, ...]
    categories: set[str]
    optional_note: str | None = None


class ComplexView(BaseModel):
    model_config = {"extra": "forbid", "strict": True}

    item_path: Path
    file_path: str
    score: float
    is_active: bool
    border_style: str
    style: str
    color: str
    tags: list[str]
    coordinates: tuple[float, ...]
    categories: set[str]
    optional_note: str | None = None


class ComplexFormatter(ComponentFormatter[ComplexData, ComplexView]):
    def transform(self, data: ComplexData) -> ComplexView:
        return ComplexView(
            item_path=data.item_path,
            file_path=data.file_path,
            score=data.score,
            is_active=data.is_active,
            border_style=data.border_style,
            style=data.style,
            color=data.color,
            tags=data.tags,
            coordinates=data.coordinates,
            categories=data.categories,
            optional_note=data.optional_note,
        )

    def to_rich(self, data: ComplexData) -> Panel:
        lines = [
            f"Path: {data.item_path.name}",
            f"File: {Path(data.file_path).name}",
            f"Score: {data.score:.1f}",
            f"Tags: {', '.join(data.tags)}",
            f"Coords: {data.coordinates[0]:.1f}",
            f"Category: {next(iter(data.categories))}",
        ]
        return Panel("\n".join(lines), border_style=data.border_style)


class ComplexMissingFormatter(ComponentFormatter[ComplexData, ComplexView]):
    def transform(self, data: ComplexData) -> ComplexView:
        return ComplexView(
            item_path=data.item_path,
            file_path=data.file_path,
            score=data.score,
            is_active=data.is_active,
            border_style=data.border_style,
            style=data.style,
            color=data.color,
            tags=data.tags,
            coordinates=data.coordinates,
            categories=data.categories,
            optional_note=data.optional_note,
        )

    def to_rich(self, data: ComplexData) -> Panel:
        lines = [
            f"File: {Path(data.file_path).name}",
            f"Score: {data.score:.1f}",
            f"Tags: {', '.join(data.tags)}",
            f"Coords: {data.coordinates[0]:.1f}",
            f"Category: {next(iter(data.categories))}",
        ]
        return Panel("\n".join(lines))


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

        assert_rich_render_shows_every_view_value(DummyFormatter, view)
        assert_rich_render_shows_every_view_value(DummyFormatter, view, expected_values=["operational", "42"])

    def test_assert_rich_render_shows_every_view_value_fails_when_field_missing(self) -> None:
        view = DummyView(message="operational", count=42)

        with pytest.raises(AssertionError, match="missing from Rich renderable output"):
            assert_rich_render_shows_every_view_value(DummyMissingFormatter, view)

    def test_assert_rich_render_shows_every_view_value_extracts_complex_types(self) -> None:
        data = ComplexData(
            item_path=Path("/workspace/project/main.py"),
            file_path="/var/log/syslog_path",
            score=98.765,
            is_active=True,
            border_style="red",
            style="bold",
            color="green",
            tags=["python", "agent"],
            coordinates=(12.34,),
            categories={"backend"},
            optional_note=None,
        )

        assert_rich_render_shows_every_view_value(ComplexFormatter, data)

    def test_assert_rich_render_shows_every_view_value_fails_when_complex_field_missing(self) -> None:
        data = ComplexData(
            item_path=Path("/workspace/project/main.py"),
            file_path="/var/log/syslog_path",
            score=98.765,
            is_active=True,
            border_style="red",
            style="bold",
            color="green",
            tags=["python", "agent"],
            coordinates=(12.34,),
            categories={"backend"},
            optional_note=None,
        )

        with pytest.raises(AssertionError, match="missing from Rich renderable output"):
            assert_rich_render_shows_every_view_value(ComplexMissingFormatter, data)

    def test_assert_rich_render_shows_every_view_value_supports_formatter_case(self) -> None:
        data = DummyData(message="operational", count=42)
        view = DummyView(message="operational", count=42)
        case = FormatterCase(data=data, view=view)

        assert_rich_render_shows_every_view_value(DummyFormatter, case)

    def test_assert_rich_render_shows_every_view_value_uses_case_render_expectations(self) -> None:
        data = DummyData(message="operational", count=42)
        view = DummyView(message="operational", count=42)
        case = FormatterCase(data=data, view=view, render_expectations=["operational"])

        assert_rich_render_shows_every_view_value(DummyFormatter, case)

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

    def test_assert_json_payload_matches_published_shape_without_formatter_dumps_base_model(self) -> None:
        view = DummyView(message="hello", count=1)
        expected = {"message": "hello", "count": 1}

        assert_json_payload_matches_published_shape(None, view, expected)

    def test_assert_json_payload_matches_published_shape_without_formatter_dumps_case_view(self) -> None:
        data = DummyData(message="hello", count=1)
        view = DummyView(message="hello", count=1)
        case = FormatterCase(data=data, view=view)
        expected = {"message": "hello", "count": 1}

        assert_json_payload_matches_published_shape(None, case, expected)

    def test_assert_rich_render_shows_every_view_value_supports_explicit_expected_values_with_case(self) -> None:
        data = DummyData(message="operational", count=42)
        view = DummyView(message="operational", count=42)
        case = FormatterCase(data=data, view=view)

        assert_rich_render_shows_every_view_value(DummyFormatter, case, expected_values=["operational"])

    def test_assert_rich_render_shows_every_view_value_handles_non_model_case_view(self) -> None:
        data = DummyData(message="operational", count=42)
        case = FormatterCase(data=data, view="plain_string_view")

        assert_rich_render_shows_every_view_value(DummyFormatter, case, expected_values=["operational"])

    def test_assert_json_payload_matches_published_shape_without_formatter_raises_on_invalid_target(self) -> None:
        with pytest.raises(TypeError, match="Target must be a BaseModel"):
            assert_json_payload_matches_published_shape(None, "invalid_string", {})

    def test_assert_json_payload_matches_published_shape_without_formatter_raises_when_case_view_not_model(
        self,
    ) -> None:
        case = FormatterCase(data="hello", view="not_a_model")

        with pytest.raises(TypeError, match="FormatterCase view must be an instance of BaseModel"):
            assert_json_payload_matches_published_shape(None, case, {})
