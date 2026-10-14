"""Pure interpretation of Claude Code stream-json output into a CliMutationOutcome."""

from __future__ import annotations

import json
import re

from dovo.common.tool_policy import ToolCapability
from dovo.core.agents.cli_mutation import CliMutationOutcome, unknown_option_detail
from dovo.core.agents.models import AgentDenial

_UPDATE_REQUIRED_DETAIL = (
    "Installed Claude Code lacks a required flag. Fix: run `claude update` or reinstall Claude Code."
)
_MAX_DIAGNOSTIC_CHARS = 2000
_TRACEBACK_MARKER = "Traceback (most recent call last):"
_STACK_FRAME = re.compile(r"^\s+at\s+\S")
_TOOL_CAPABILITIES: dict[str, ToolCapability] = {
    "Bash": ToolCapability.SHELL,
    "Read": ToolCapability.READ,
    "Grep": ToolCapability.READ,
    "Glob": ToolCapability.READ,
    "Edit": ToolCapability.WRITE,
    "Write": ToolCapability.WRITE,
    "NotebookEdit": ToolCapability.WRITE,
    "WebFetch": ToolCapability.NETWORK,
}
_MCP_TOOL_PREFIX = "mcp__"
_DENIAL_MESSAGE = "Permission to use {tool} was denied"
_DENIAL_WORDING = re.compile(r"^Permission to use .+? has been denied")
_BARE_DENIAL_WORDING = re.compile(
    r"^Permission to use .+? has been denied\.\s*$"
)  # a deny rule ends the sentence; a mode denial adds "because ..."


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


def _tool_capability(tool_name: str) -> ToolCapability | None:
    """Map a Claude Code tool name to the Dovo capability that grants it, or None when unknown."""
    capability = _TOOL_CAPABILITIES.get(tool_name)
    if capability is None and tool_name.startswith(_MCP_TOOL_PREFIX):
        return ToolCapability.MCP

    return capability


def _structured_denials(terminal: dict[str, object]) -> list[tuple[str | None, AgentDenial]]:
    """Return each well-formed permission_denials entry as its tool_use_id (when present) and a denial, in array order."""
    entries = terminal.get("permission_denials")
    if not isinstance(entries, list):
        return []

    denials: list[tuple[str | None, AgentDenial]] = []
    for entry in entries:
        name = entry.get("tool_name") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name:
            continue

        tool_use_id = entry.get("tool_use_id")
        denial = AgentDenial(
            tool=name,
            message=_DENIAL_MESSAGE.format(tool=name),
            capability=_tool_capability(name),
            by_rule=False,
        )
        denials.append((tool_use_id if isinstance(tool_use_id, str) else None, denial))

    return denials


def _content_blocks(event: dict[str, object], event_type: str) -> list[dict[str, object]]:
    """Return the dict content blocks of an event of the given type, or [] when its content is not a list."""
    if event.get("type") != event_type:
        return []

    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return []

    return [block for block in content if isinstance(block, dict)]


def _tool_use_names(events: list[dict[str, object]]) -> dict[str, str]:
    """Return tool_use block id -> tool name across every assistant event."""
    names: dict[str, str] = {}
    for event in events:
        for block in _content_blocks(event, "assistant"):
            block_id = block.get("id")
            name = block.get("name")
            if block.get("type") == "tool_use" and isinstance(block_id, str) and isinstance(name, str):
                names[block_id] = name

    return names


def _block_text(block: dict[str, object]) -> str:
    """Return the text of a tool_result block whether its content is a string or a list of text items."""
    content = block.get("content")
    if isinstance(content, str):
        return content

    if not isinstance(content, list):
        return ""

    texts = [
        item["text"]
        for item in content
        if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
    ]
    return "\n".join(texts)


def _denied_block_text(block: dict[str, object]) -> str | None:
    """Return the stripped text of an errored tool_result block that opens with the denial wording, else None."""
    if block.get("type") != "tool_result" or block.get("is_error") is not True:
        return None

    text = _block_text(block).strip()
    return text if _DENIAL_WORDING.match(text) else None


def _denied_results(events: list[dict[str, object]]) -> dict[str, str]:
    """Return tool_use_id -> stripped denial text for each denied user tool_result, in stream order, first occurrence only."""
    denied: dict[str, str] = {}
    for event in events:
        for block in _content_blocks(event, "user"):
            text = _denied_block_text(block)
            tool_use_id = block.get("tool_use_id")
            if text is not None and isinstance(tool_use_id, str):
                denied.setdefault(tool_use_id, text)

    return denied


def _collect_denials(events: list[dict[str, object]], terminal: dict[str, object]) -> list[AgentDenial]:
    """Return the terminal result's structured denials followed by event denials not already covered.

    A structured denial whose denied tool_result carries the bare deny-rule wording is upgraded to by_rule, since the
    structured entry alone never says why the call was denied.
    """
    structured = _structured_denials(terminal)
    denied = _denied_results(events)
    names = _tool_use_names(events)
    covered_ids = {tool_use_id for tool_use_id, _ in structured if tool_use_id is not None}

    denials = [
        denial.model_copy(update={"by_rule": _is_rule_denial(denied.get(tool_use_id or ""))})
        for tool_use_id, denial in structured
    ]
    for tool_use_id, text in denied.items():
        if tool_use_id in covered_ids:
            continue

        tool = names.get(tool_use_id, "unknown")
        denials.append(
            AgentDenial(
                tool=tool,
                message=_DENIAL_MESSAGE.format(tool=tool),
                capability=_tool_capability(tool),
                by_rule=_is_rule_denial(text),
            )
        )

    return denials


def _is_rule_denial(text: str | None) -> bool:
    """Return whether a denial text is the bare deny-rule wording rather than a mode denial with a 'because' clause."""
    return text is not None and _BARE_DENIAL_WORDING.match(text) is not None


def _result_outcome(event: dict[str, object], events: list[dict[str, object]]) -> CliMutationOutcome:
    """Return the finished outcome carrying the run's denials, or the error outcome for a failed terminal result."""
    detail = _result_error_detail(event)
    if detail is not None:
        return _error_outcome(detail)

    result = event.get("result")
    return CliMutationOutcome(
        status="finished",
        result_text=result if isinstance(result, str) and result else None,
        denials=_collect_denials(events, event),
    )


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

    return _result_outcome(terminal, events)
