"""Shared formatters."""


def format_warning_bullets(warnings: list[str]) -> list[str]:
    """Format warnings as bullet lines with indented continuations."""
    lines: list[str] = []
    for warning in warnings:
        parts = warning.splitlines() or [""]
        lines.append(f"- {parts[0]}")
        for continuation in parts[1:]:
            lines.append(f"  {continuation}")
    return lines
