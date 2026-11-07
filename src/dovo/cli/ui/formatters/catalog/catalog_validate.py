"""ComponentFormatter for CatalogValidateResult."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import ClassVar

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from dovo.cli.ui.formatters.catalog.catalog_views import CatalogValidateView
from dovo.cli.ui.formatters.catalog.common import CatalogFormatter
from dovo.cli.ui.formatters.common import with_diagnostics
from dovo.cli.ui.formatters.header import header_fields
from dovo.common.models import Remediation
from dovo.common.utils import enum_value
from dovo.core.catalog.models import CatalogItemType, CatalogValidateResult

_CODE_SUFFIX_RE = re.compile(r"\s*\(([A-Z][A-Z0-9_]*)\)\.?\s*$")


def _split_issue_code(message: str) -> tuple[str, str]:
    """Split a validate message into (code, detail) by stripping a trailing '(CODE).' suffix."""
    match = _CODE_SUFFIX_RE.search(message)
    if not match:
        return "", message
    return match.group(1), message[: match.start()].rstrip()


def _build_target_lines(view: CatalogValidateView) -> Text:
    """Render the target, item type and resolved path lines."""
    lines = [f"Target: {view.target}", f"Type: {view.item_type}"]
    if view.resolved_path is not None:
        lines.append(f"Path: {view.resolved_path}")
    return Text("\n".join(lines))


def _build_issues_table(
    title: str, messages: list[str], remediations: list[Remediation], *, show_remediation: bool, default_code: str = ""
) -> Table:
    """Build a Code/Details[/Remediation] Rich table, pairing remediations[i].message with messages[i].

    A message without a trailing code shows default_code in the Code column.
    """
    table = Table(title=title, title_justify="left", show_header=True)
    table.add_column("Code", no_wrap=True)
    table.add_column("Details")
    if show_remediation:
        table.add_column("Remediation")

    for index, message in enumerate(messages):
        code, detail = _split_issue_code(message)
        code = code or default_code
        if show_remediation:
            remediation = remediations[index].message if index < len(remediations) else ""
            table.add_row(code, detail, remediation)
        else:
            table.add_row(code, detail)

    return table


def _render(view: CatalogValidateView) -> RenderableType:
    """Render the target lines, the Errors/Warnings tables, and a closing success line when error_code is None."""
    renderables: list[RenderableType] = [_build_target_lines(view)]
    if view.errors:
        renderables.append(
            _build_issues_table(
                "Errors", view.errors, view.remediations, show_remediation=True, default_code=view.error_code or ""
            )
        )
    if view.warnings:
        renderables.append(_build_issues_table("Warnings", view.warnings, [], show_remediation=False))
    if view.error_code is None:
        renderables.append(Text(f"{view.item_type.capitalize()} is valid.", style="green"))
    return Group(*renderables)


class CatalogValidateFormatter(CatalogFormatter[CatalogValidateResult, CatalogValidateView]):
    """Formatter for catalog validate command results."""

    event_types: ClassVar[Mapping[CatalogItemType, str]] = {
        CatalogItemType.BLUEPRINT: "blueprint.validate",
        CatalogItemType.STEP: "step.validate",
    }

    def transform(self, data: CatalogValidateResult) -> CatalogValidateView:
        """Derive the presentation-ready view from CatalogValidateResult.

        Args:
            data: Domain CatalogValidateResult.

        Returns:
            CatalogValidateView with flat, JSON-faithful fields.
        """
        return CatalogValidateView(
            **header_fields(data),
            target=data.target,
            resolved_path=data.resolved_path.as_posix() if data.resolved_path is not None else None,
            item_type=enum_value(data.item_type),
        )

    def to_rich(self, data: CatalogValidateResult) -> RenderableType:
        """Render the target lines, Errors/Warnings tables and success line, followed by diagnostic_lines(diagnostics)."""
        view = self.transform(data)
        return with_diagnostics(_render(view), view.diagnostics)
