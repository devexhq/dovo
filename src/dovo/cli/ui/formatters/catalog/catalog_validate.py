"""ComponentFormatter for CatalogValidateResult."""

from __future__ import annotations

import re
from typing import Any

from rich.console import Group
from rich.table import Table
from rich.text import Text

from dovo.cli.ui.formatters.catalog.catalog_views import CatalogValidateView
from dovo.common.types import ComponentFormatter
from dovo.common.utils import enum_value
from dovo.core.catalog.models import CatalogValidateResult

_CODE_SUFFIX_RE = re.compile(r"\s*\(([A-Z][A-Z0-9_]*)\)\.?\s*$")


def _split_issue_code(message: str) -> tuple[str, str]:
    """Split a validate message into (code, detail) by stripping a trailing '(CODE).' suffix."""
    match = _CODE_SUFFIX_RE.search(message)
    if not match:
        return "", message
    return match.group(1), message[: match.start()].rstrip()


def _build_status_banner(view: CatalogValidateView) -> Text:
    """Render the status banner with target, item type, and resolved path.

    ``view.status_label`` is derived in ``transform()``, not here: the only decision made in this
    function is a display color for an already-known label, never a pass/fail verdict from raw fields.
    """
    color = "green" if view.status_label == "PASSED" else "red"
    lines = [f"[bold {color}]{view.status_label}[/]", f"Target: {view.target}"]
    if view.item_type is not None:
        lines.append(f"Type: {view.item_type}")
    if view.resolved_path is not None:
        lines.append(f"Path: {view.resolved_path}")
    return Text.from_markup("\n".join(lines))


def _build_issues_table(title: str, messages: list[str], fixes: list[str], *, show_remediation: bool) -> Table:
    """Build a Code/Details[/Remediation] Rich table from validate messages."""
    table = Table(title=title, title_justify="left", show_header=True)
    table.add_column("Code", no_wrap=True)
    table.add_column("Details")
    if show_remediation:
        table.add_column("Remediation")

    for index, message in enumerate(messages):
        code, detail = _split_issue_code(message)
        if show_remediation:
            fix = fixes[index] if index < len(fixes) else ""
            table.add_row(code, detail, fix)
        else:
            table.add_row(code, detail)

    return table


class CatalogValidateFormatter(ComponentFormatter[CatalogValidateResult, CatalogValidateView]):
    """Formatter for catalog validate command results."""

    def transform(self, data: CatalogValidateResult) -> CatalogValidateView:
        """Derive the presentation-ready view from CatalogValidateResult.

        Args:
            data: Domain CatalogValidateResult.

        Returns:
            CatalogValidateView with flat, JSON-faithful fields.
        """
        return CatalogValidateView(
            status=enum_value(data.status),
            valid=data.valid,
            status_label="PASSED" if data.ok else "FAILED",
            target=data.target,
            resolved_path=data.resolved_path.as_posix() if data.resolved_path is not None else None,
            item_type=data.item_type,
            errors=list(data.errors),
            warnings=list(data.warnings),
            fixes=list(data.fixes),
        )

    def to_rich(self, data: CatalogValidateResult) -> Any:
        """Render the status banner plus Errors/Warnings tables."""
        view = self.transform(data)
        renderables: list[Any] = [_build_status_banner(view)]
        if view.errors:
            renderables.append(_build_issues_table("Errors", view.errors, view.fixes, show_remediation=True))
        if view.warnings:
            renderables.append(_build_issues_table("Warnings", view.warnings, [], show_remediation=False))
        return Group(*renderables)
