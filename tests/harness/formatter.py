"""Tier 2 FormatterCase harness and contract assertion helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel
from rich.console import Console

from dovo.cli.ui.formatters.base import ComponentFormatter


@dataclass(frozen=True)
class FormatterCase[TData, TView]:
    """One formatter scenario: the domain input, intermediate view, and render expectations.

    Attributes:
        data: Input domain model or event.
        view: Expected presentation view model.
        render_expectations: Explicit list of strings expected in Rich output.
    """

    data: TData
    view: TView
    render_expectations: list[str] = field(default_factory=list)

    @classmethod
    def identity(cls, data: TData, render_expectations: list[str]) -> FormatterCase[TData, TData]:
        """Build a case for a formatter whose view is its input, so the data is written once."""
        return FormatterCase(data=data, view=data, render_expectations=render_expectations)


def render_rich(renderable: object, width: int = 160) -> str:
    """Render a Rich renderable directly to plain text at a strictly pinned console width.

    Args:
        renderable: Any Rich-compatible renderable object.
        width: Authoritatively pinned console width in columns.

    Returns:
        Rendered text output without ANSI color escapes.
    """
    console = Console(width=width, color_system=None, record=True)
    console.print(renderable)
    return console.export_text()


def assert_rich_render_shows_every_view_value(
    formatter_cls: type[ComponentFormatter[Any, Any]] | ComponentFormatter[Any, Any],
    target: object,
    expected_values: list[str],
) -> None:
    """Assert that every expected view value reaches the Rich renderable output.

    Args:
        formatter_cls: Formatter class or instance conforming to ComponentFormatter.
        target: Domain data or FormatterCase rendered by the formatter.
        expected_values: Strings that must appear in the rendered output.

    Raises:
        AssertionError: If expected_values is empty or any expected value is missing.
    """
    if not expected_values:
        raise AssertionError("no render expectations: list the view values this case must render")
    formatter = formatter_cls() if isinstance(formatter_cls, type) else formatter_cls
    data = target.data if isinstance(target, FormatterCase) else target

    rendered = render_rich(formatter.to_rich(data))

    missing = [val for val in expected_values if val not in rendered]
    if missing:
        raise AssertionError(f"Required view value(s) {missing!r} missing from Rich renderable output:\n{rendered}")


def assert_transform_derives_expected_view[TData, TView: BaseModel](
    formatter_cls: type[ComponentFormatter[TData, TView]] | ComponentFormatter[TData, TView],
    domain_result: TData,
    expected_view: TView,
) -> None:
    """Assert that formatter.transform(domain_result) derives the expected typed view model.

    Args:
        formatter_cls: Formatter class or instance conforming to ComponentFormatter.
        domain_result: Input domain result or event to transform.
        expected_view: Expected presentation view model.

    Raises:
        AssertionError: If actual view does not equal expected view.
    """
    formatter = formatter_cls() if isinstance(formatter_cls, type) else formatter_cls
    actual_view = formatter.transform(domain_result)
    assert actual_view == expected_view


def assert_json_payload_matches_published_shape(
    formatter_cls: type[ComponentFormatter[Any, Any]] | ComponentFormatter[Any, Any],
    target: object,
    expected_wire_dict: dict[str, Any],
) -> None:
    """Assert that JSON serialization strictly matches the published wire format dict.

    Args:
        formatter_cls: Formatter class or instance conforming to ComponentFormatter.
        target: Domain data or FormatterCase to serialize.
        expected_wire_dict: Exact expected wire format dictionary.

    Raises:
        AssertionError: If serialized JSON payload does not match expected_wire_dict.
    """
    formatter = formatter_cls() if isinstance(formatter_cls, type) else formatter_cls
    data = target.data if isinstance(target, FormatterCase) else target
    actual_payload = formatter.to_json_serializable(data)

    assert actual_payload == expected_wire_dict, (
        f"Wire format mismatch:\nExpected: {expected_wire_dict!r}\nActual:   {actual_payload!r}"
    )
