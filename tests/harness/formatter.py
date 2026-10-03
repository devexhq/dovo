"""Tier 2 FormatterCase harness and contract assertion helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from rich.console import Console

from dovo.common.types import ComponentFormatter


@dataclass
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


def _format_view_value_for_search(field_name: str, val: object) -> str:
    """Format an individual view model field value for string containment searching.

    Args:
        field_name: Name of the field being inspected.
        val: Non-null field value to format.

    Returns:
        Formatted substring expected to appear in render output.
    """
    if isinstance(val, Path) or field_name.endswith("_path"):
        return Path(str(val)).name
    if isinstance(val, float):
        return f"{val:.1f}"
    return str(val)


_SKIP_FIELDS: frozenset[str] = frozenset({"border_style", "style", "color"})


def _is_inspectable_value(val: object) -> bool:
    """Return True if val is a non-null, non-boolean inspectable field value."""
    return val is not None and not isinstance(val, bool)


def _collect_field_values(field_name: str, val: object) -> list[str]:
    """Collect formatted search strings from a field value or collection."""
    if isinstance(val, (list, tuple, set)):
        return [_format_view_value_for_search(field_name, item) for item in val if _is_inspectable_value(item)]
    return [_format_view_value_for_search(field_name, val)]


def _extract_required_view_values(view_instance: BaseModel) -> list[str]:
    """Extract string representations for required or non-null fields on a view model.

    Args:
        view_instance: Pydantic view model to inspect.

    Returns:
        List of required substring values expected in rendered output.
    """
    expected_values: list[str] = []
    for name in type(view_instance).model_fields:
        if name in _SKIP_FIELDS:
            continue
        val = getattr(view_instance, name, None)
        if not _is_inspectable_value(val):
            continue
        expected_values.extend(_collect_field_values(name, val))
    return expected_values


def _resolve_case_search_values(case: FormatterCase[object, object], expected_values: list[str] | None) -> list[str]:
    """Resolve expected search strings from a FormatterCase."""
    if expected_values is not None:
        return expected_values
    if case.render_expectations:
        return case.render_expectations
    if isinstance(case.view, BaseModel):
        return _extract_required_view_values(case.view)
    return []


def _resolve_direct_search_values(
    formatter: ComponentFormatter[Any, Any],
    target: object,
    expected_values: list[str] | None,
) -> list[str]:
    """Resolve expected search strings from domain data or view model."""
    if expected_values is not None:
        return expected_values
    try:
        derived_view: object = formatter.transform(target)
    except Exception:
        derived_view = target
    if isinstance(derived_view, BaseModel):
        return _extract_required_view_values(derived_view)
    if isinstance(target, BaseModel):
        return _extract_required_view_values(target)
    return []


def assert_rich_render_shows_every_view_value(
    formatter_cls: type[ComponentFormatter[Any, Any]] | ComponentFormatter[Any, Any],
    target: object,
    expected_values: list[str] | None = None,
) -> None:
    """Assert that all semantic view values reach the Rich renderable output.

    Args:
        formatter_cls: Formatter class or instance conforming to ComponentFormatter.
        target: Domain data, FormatterCase, or view model instance rendered by the formatter.
        expected_values: Optional list of explicit strings expected in render.
            If None, required non-null fields on the view model are verified.

    Raises:
        AssertionError: If any required view field or expected value is missing.
    """
    formatter = formatter_cls() if isinstance(formatter_cls, type) else formatter_cls
    if isinstance(target, FormatterCase):
        data = target.data
        values_to_check = _resolve_case_search_values(target, expected_values)
    else:
        data = target
        values_to_check = _resolve_direct_search_values(formatter, target, expected_values)

    rendered = render_rich(formatter.to_rich(data))

    missing = [val for val in values_to_check if val not in rendered]
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


def _dump_without_formatter(target: object) -> dict[str, Any]:
    """Serialize target when no formatter is provided."""
    if isinstance(target, FormatterCase):
        if isinstance(target.view, BaseModel):
            return target.view.model_dump(mode="json")
        raise TypeError(f"FormatterCase view must be an instance of BaseModel, got {type(target.view).__name__}")
    if isinstance(target, BaseModel):
        return target.model_dump(mode="json")
    raise TypeError(f"Target must be a BaseModel when no formatter is provided, got {type(target).__name__}")


def assert_json_payload_matches_published_shape(
    formatter_cls: type[ComponentFormatter[Any, Any]] | ComponentFormatter[Any, Any] | None,
    target: object,
    expected_wire_dict: dict[str, Any],
) -> None:
    """Assert that JSON serialization strictly matches the published wire format dict.

    Args:
        formatter_cls: Formatter class or instance conforming to ComponentFormatter,
            or None if asserting directly on a pre-derived view model.
        target: Domain data, FormatterCase, or view model instance to serialize.
        expected_wire_dict: Exact expected wire format dictionary.

    Raises:
        AssertionError: If serialized JSON payload does not match expected_wire_dict.
        TypeError: If target cannot be serialized to JSON.
    """
    if formatter_cls is not None:
        formatter = formatter_cls() if isinstance(formatter_cls, type) else formatter_cls
        data = target.data if isinstance(target, FormatterCase) else target
        actual_payload = formatter.to_json_serializable(data)
    else:
        actual_payload = _dump_without_formatter(target)

    assert actual_payload == expected_wire_dict, (
        f"Wire format mismatch:\nExpected: {expected_wire_dict!r}\nActual:   {actual_payload!r}"
    )
