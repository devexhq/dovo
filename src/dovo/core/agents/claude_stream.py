"""Pure interpretation of Claude Code stream-json output into a CliMutationOutcome."""

from __future__ import annotations

import json
import re

from dovo.core.agents.cli_mutation import CliMutationOutcome, unknown_option_detail

_UPDATE_REQUIRED_DETAIL = (
    "Installed Claude Code lacks a required flag. Fix: run `claude update` or reinstall Claude Code."
)
_MAX_DIAGNOSTIC_CHARS = 2000
_TRACEBACK_MARKER = "Traceback (most recent call last):"
_STACK_FRAME = re.compile(r"^\s+at\s+\S")


def _sanitize_diagnostic(text: str) -> str:
    """Return text as one trimmed line of at most 2000 characters with Python and Node stack traces removed."""
    before_traceback = text.split(_TRACEBACK_MARKER, 1)[0]
    kept_lines = [line for line in before_traceback.splitlines() if not _STACK_FRAME.match(line)]
    single_line = " ".join("\n".join(kept_lines).split())
    if len(single_line) <= _MAX_DIAGNOSTIC_CHARS:
        return single_line

    return single_line[: _MAX_DIAGNOSTIC_CHARS - 1].rstrip() + "…"


def _error_outcome(detail: str) -> CliMutationOutcome:
    """Return an error outcome carrying the sanitized detail."""
    return CliMutationOutcome(status="error", error_detail=_sanitize_diagnostic(detail))


def _parse_line(line: str) -> dict[str, object] | str:
    """Return the JSON object a stdout line holds, or the reason it is not one."""
    try:
        value: object = json.loads(line)
    except json.JSONDecodeError as error:
        return str(error)

    if not isinstance(value, dict):
        return f"expected a JSON object, got {type(value).__name__}"

    return {str(key): item for key, item in value.items()}


def _parse_events(stdout_text: str) -> tuple[list[dict[str, object]], str | None]:
    """Return every JSON-object line of stdout in order and the first parse failure reason, if any."""
    events: list[dict[str, object]] = []
    first_failure: str | None = None
    for line in stdout_text.splitlines():
        if not line.strip():
            continue

        parsed = _parse_line(line.strip())
        if isinstance(parsed, str):
            first_failure = first_failure or parsed
        else:
            events.append(parsed)

    return events, first_failure


def _terminal_result(events: list[dict[str, object]]) -> dict[str, object] | None:
    """Return the last event with type 'result', or None when the stream has none."""
    for event in reversed(events):
        if event.get("type") == "result":
            return event

    return None


def _result_error_detail(event: dict[str, object]) -> str | None:
    """Return the error detail a terminal result describes, or None when it succeeded.

    is_error is checked before subtype: a result can carry subtype 'success' with is_error true.
    """
    subtype = event.get("subtype")
    has_error_subtype = isinstance(subtype, str) and subtype.startswith("error")
    if event.get("is_error") is not True and not has_error_subtype:
        if subtype == "success":
            return None

        return f"unsupported Claude Code result status '{subtype if isinstance(subtype, str) else 'unknown'}'"

    terminal_reason = event.get("terminal_reason")
    label = subtype if has_error_subtype else terminal_reason
    detail = "Claude Code result error"
    if isinstance(label, str) and label.strip():
        detail += f" ({label})"

    result = event.get("result")
    if isinstance(result, str) and result.strip():
        detail += f": {result}"

    return detail


def _result_outcome(event: dict[str, object]) -> CliMutationOutcome:
    """Return the finished or error outcome for a terminal result."""
    detail = _result_error_detail(event)
    if detail is not None:
        return _error_outcome(detail)

    result = event.get("result")
    return CliMutationOutcome(status="finished", result_text=result if isinstance(result, str) and result else None)


def _exit_error_outcome(
    returncode: int, events: list[dict[str, object]], stdout_text: str, stderr_text: str
) -> CliMutationOutcome:
    """Return the error outcome for a non-zero exit from the terminal result error, else stderr, else stdout, else the code.

    Stdout is a candidate only when it held no JSON events, so a stream-json dump never becomes the detail.
    """
    if unknown_option_detail(stderr_text, _UPDATE_REQUIRED_DETAIL) is not None:
        return _error_outcome(_UPDATE_REQUIRED_DETAIL)

    terminal = _terminal_result(events)
    candidates = [
        _result_error_detail(terminal) if terminal is not None else None,
        stderr_text,
        stdout_text if not events else None,
    ]
    for candidate in candidates:
        if candidate is not None and _sanitize_diagnostic(candidate):
            return _error_outcome(candidate)

    if events and terminal is None:
        return _error_outcome(f"Claude Code stream ended without a result (exit {returncode})")

    return _error_outcome(f"exit {returncode}")


def classify_claude_output(returncode: int, stdout_text: str, stderr_text: str) -> CliMutationOutcome:
    """Classify a Claude Code process exit code and stream-json output into a CliMutationOutcome."""
    events, parse_error = _parse_events(stdout_text)
    if returncode != 0:
        return _exit_error_outcome(returncode, events, stdout_text, stderr_text)

    if parse_error is not None:
        return _error_outcome(f"invalid stream-json from Claude Code: {parse_error}")

    terminal = _terminal_result(events)
    if terminal is None:
        return _error_outcome("Claude Code stream ended without a result")

    return _result_outcome(terminal)
