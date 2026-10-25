"""Shared formatters."""

from collections.abc import Sequence

from dovo.common.models import Remediation


def format_warning_bullets(warnings: list[str]) -> list[str]:
    """Format warnings as bullet lines with indented continuations."""
    lines: list[str] = []
    for warning in warnings:
        parts = warning.splitlines() or [""]
        lines.append(f"- {parts[0]}")
        for continuation in parts[1:]:
            lines.append(f"  {continuation}")
    return lines


def format_remediations(remediations: Sequence[Remediation]) -> str:
    """Return a 'Fix:' header and one '- <message>' line per remediation, or '' when there are none."""
    if not remediations:
        return ""
    return "\n".join(["Fix:", *(f"- {remediation.message}" for remediation in remediations)])
