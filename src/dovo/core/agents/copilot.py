"""GitHub Copilot CLI direct-mutation agent adapter."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dovo.common.process import run_isolated_process
from dovo.common.tool_policy import ToolCapability, ToolPolicy, ToolRule
from dovo.core.agents.base import ProviderSpec
from dovo.core.agents.cli_mutation import (
    CliDirectMutationAdapter,
    CliMutationOutcome,
    CliMutationRunRequest,
)
from dovo.core.agents.credentials import missing_credential_error, resolve_credential
from dovo.core.agents.models import AgentDenial, AgentInvocationContext, AgentRequest
from dovo.core.agents.tools import (
    PolicyRoots,
    policy_rules,
    resolve_policy_roots,
    tool_policy_unsupported_message,
)

COPILOT_TOKEN_ENVS = ("GH_TOKEN", "GITHUB_TOKEN")
COPILOT_CONTROL_ENVS = ("COPILOT_MODEL", "COPILOT_ALLOW_ALL", "COPILOT_GITHUB_TOKEN", "COPILOT_HOME")
_UPDATE_REQUIRED_DETAIL = (
    "Installed GitHub Copilot CLI lacks a required tool-policy control. "
    "Fix: run `copilot update` or reinstall the GitHub Copilot CLI."
)
_PATH_GLOB_ALL = (None, "**")
_PATH_CAPABILITIES = (ToolCapability.READ, ToolCapability.WRITE)
_UNRESTRICTED_FLAGS = ("--allow-all-tools", "--allow-all-paths", "--allow-all-urls")
_HTTP_SCHEMES = ("https", "http")


def resolve_copilot_token() -> str | None:
    """Resolve the Copilot auth token from the environment at call time."""
    return resolve_credential(COPILOT_TOKEN_ENVS)


def _extract_text(value: object) -> str | None:
    """Extract text content from Copilot event payload dictionary."""
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return None
    for key in ("content", "response"):
        val = value.get(key)
        if isinstance(val, str):
            return val
    message = value.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    return None


def _extract_exit_code(payload: dict[str, Any]) -> int | None:
    """Extract integer exit code from Copilot event payload dictionary."""
    raw_exit = payload.get("exitCode") if "exitCode" in payload else payload.get("exit_code")
    if isinstance(raw_exit, int):
        return raw_exit
    if isinstance(raw_exit, str) and raw_exit.isdigit():
        return int(raw_exit)
    return None


def _process_copilot_event(data: dict[str, Any], current_text: str | None) -> tuple[str | None, int | None]:
    """Update assistant text and extract exit code from a single Copilot event."""
    event_type = data.get("type")
    payload = data.get("data")
    if not isinstance(payload, dict):
        return current_text, None
    if event_type == "assistant.message":
        text = _extract_text(payload)
        return (text if text is not None else current_text), None
    if event_type == "result":
        exit_code = _extract_exit_code(payload)
        text = _extract_text(payload)
        new_text = text if (text is not None and current_text is None) else current_text
        return new_text, exit_code
    return current_text, None


_TOOL_CAPABILITIES: dict[str, ToolCapability] = {
    "bash": ToolCapability.SHELL,
    "read_bash": ToolCapability.SHELL,
    "stop_bash": ToolCapability.SHELL,
    "list_bash": ToolCapability.SHELL,
    "view": ToolCapability.READ,
    "grep": ToolCapability.READ,
    "glob": ToolCapability.READ,
    "create": ToolCapability.WRITE,
    "edit": ToolCapability.WRITE,
    "web_fetch": ToolCapability.NETWORK,
}


def _denial_capability(tool_name: str) -> ToolCapability | None:
    """Map a Copilot tool name to the Dovo capability that grants it, or None when unknown."""
    return _TOOL_CAPABILITIES.get(tool_name)


def _record_tool_name(payload: dict[str, Any], tool_names: dict[str, str]) -> None:
    """Remember the tool name of a tool.execution_start payload under its toolCallId."""
    call_id, tool_name = payload.get("toolCallId"), payload.get("toolName")
    if isinstance(call_id, str) and isinstance(tool_name, str):
        tool_names[call_id] = tool_name


def _denial_from_complete(payload: dict[str, Any], tool_names: dict[str, str]) -> AgentDenial | None:
    """Return the denial a failed tool.execution_complete payload describes, or None when it failed for another reason."""
    error = payload.get("error")
    if payload.get("success") is not False or not isinstance(error, dict):
        return None

    message = error.get("message")
    code = error.get("code")
    if not isinstance(message, str) or (code != "denied" and "Permission denied" not in message):
        return None

    call_id = payload.get("toolCallId")
    tool = tool_names.get(call_id, "unknown") if isinstance(call_id, str) else "unknown"

    return AgentDenial(
        tool=tool,
        message=message,
        capability=_denial_capability(tool),
        by_rule=code == "denied" and "following rules" in message,
    )


def _collect_denials(events: list[dict[str, Any]]) -> list[AgentDenial]:
    """Return one AgentDenial per denied tool.execution_complete event.

    The tool name is only on the matching tool.execution_start event, so the two are joined on toolCallId.
    """
    tool_names: dict[str, str] = {}
    denials: list[AgentDenial] = []
    for event in events:
        payload = event.get("data")
        if not isinstance(payload, dict):
            continue

        if event.get("type") == "tool.execution_start":
            _record_tool_name(payload, tool_names)
        elif event.get("type") == "tool.execution_complete":
            denial = _denial_from_complete(payload, tool_names)
            if denial is not None:
                denials.append(denial)

    return denials


def _parse_jsonl(stdout_text: str) -> tuple[str | None, int | None, str | None, list[AgentDenial]]:
    """Parse JSONL output lines from the Copilot CLI stream."""
    assistant_text: str | None = None
    result_exit_code: int | None = None
    events: list[dict[str, Any]] = []
    for raw_line in stdout_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            return None, None, f"invalid JSONL from Copilot CLI: {exc}", []
        if isinstance(data, dict):
            events.append(data)
            assistant_text, code = _process_copilot_event(data, assistant_text)
            if code is not None:
                result_exit_code = code
    return assistant_text, result_exit_code, None, _collect_denials(events)


def _classify_copilot_output(completed_code: int, stdout_text: str, stderr_text: str) -> CliMutationOutcome:
    """Classify CLI process exit code and output text into a CliMutationOutcome."""
    if completed_code != 0:
        if "unknown option" in stderr_text.lower():
            return CliMutationOutcome(status="error", error_detail=_UPDATE_REQUIRED_DETAIL)

        detail = stderr_text.strip() or stdout_text.strip() or f"exit {completed_code}"
        return CliMutationOutcome(status="error", error_detail=detail)

    assistant_text, exit_code, parse_error, denials = _parse_jsonl(stdout_text)
    if parse_error is not None:
        return CliMutationOutcome(status="error", error_detail=parse_error)
    if exit_code is not None and exit_code != 0:
        return CliMutationOutcome(
            status="error",
            error_detail=f"Copilot CLI result exit code {exit_code}",
            result_text=assistant_text,
        )
    if assistant_text is None and not stdout_text.strip():
        return CliMutationOutcome(status="error", error_detail="empty Copilot CLI output")
    return CliMutationOutcome(
        status="finished", result_text=assistant_text or stdout_text.strip() or None, denials=denials
    )


@dataclass(frozen=True)
class CopilotPolicyArgs:
    """Rendered Copilot CLI flags and the isolated COPILOT_HOME for one resolved policy."""

    flags: tuple[str, ...]
    home: Path | None


def _render_rule_flags(rule: ToolRule, *, deny: bool) -> list[str] | str:
    """Return the --allow-tool/--deny-tool flags for one shell or MCP rule, or an error."""
    if rule.capability != ToolCapability.SHELL:
        return f"{rule.capability.value} rules cannot be rendered for Copilot"

    pattern = rule.pattern
    if pattern is None:
        permission = "shell"
    elif pattern.endswith(" *"):
        permission = f"shell({pattern.removesuffix(' *')}:*)"
    else:
        permission = f"shell({pattern})"

    return ["--deny-tool" if deny else "--allow-tool", permission]


def _render_url_flags(rule: ToolRule, *, deny: bool) -> list[str] | str:
    """Return the https and http --allow-url/--deny-url flags for one network rule, or an error."""
    if rule.capability != ToolCapability.NETWORK:
        return f"{rule.capability.value} rules are not network rules"

    if rule.pattern is None:
        return ["--deny-tool", "url"] if deny else ["--allow-all-urls"]

    flag = "--deny-url" if deny else "--allow-url"

    return [part for scheme in _HTTP_SCHEMES for part in (flag, f"{scheme}://{rule.pattern}")]


def _render_command_rules(rules: list[ToolRule], *, deny: bool) -> list[str] | str:
    """Return the flags for the shell and network rules, or the first error; read/write/mcp rules are rejected."""
    flags: list[str] = []
    for rule in rules:
        if rule.capability == ToolCapability.SHELL:
            rendered = _render_rule_flags(rule, deny=deny)
        elif rule.capability == ToolCapability.NETWORK:
            rendered = _render_url_flags(rule, deny=deny)
        else:
            return f"{rule.capability.value} {'deny' if deny else 'allow'} rules cannot be rendered for Copilot"

        if isinstance(rendered, str):
            return rendered

        flags.extend(rendered)

    return flags


def _render_unrestricted(policy: ToolPolicy) -> CopilotPolicyArgs | str:
    """Return the fully permissive flags followed by the policy's deny flags; allow rules are not rendered."""
    denies = _render_command_rules(policy.deny, deny=True)
    if isinstance(denies, str):
        return denies

    return CopilotPolicyArgs(flags=(*_UNRESTRICTED_FLAGS, *denies), home=None)


def _granted_roots(policy: ToolPolicy, capability: ToolCapability) -> set[str] | str:
    """Return the roots a whole-root read/write rule grants for read or write, or why a rule cannot be preserved."""
    roots: set[str] = set()
    for rule in policy.allow:
        if rule.capability != capability:
            continue

        if rule.pattern not in _PATH_GLOB_ALL:
            return f"{capability.value} pattern {rule.pattern!r} is narrower than a whole root and cannot be preserved"

        roots.add(rule.root or "worktree")

    return roots


def _scratch_flags(granted: list[set[str]], roots: PolicyRoots) -> list[str] | str:
    """Return the --add-dir flags for the roots granted to read and write, or why they cannot be preserved."""
    available = [granted_roots for granted_roots in granted if granted_roots]
    if not available:
        return []

    if any("worktree" not in granted_roots for granted_roots in available):
        return "a read or write grant that omits the worktree cannot be preserved"

    scratch_flags = ["scratch" in granted_roots for granted_roots in available]
    if any(scratch_flags) != all(scratch_flags):
        return "scratch must be granted to every available path tool or to none"

    if not any(scratch_flags):
        return []

    if roots.scratch is None:
        return "a scratch grant requires a scratch root"

    return ["--add-dir", str(roots.scratch)]


def _path_scope(policy: ToolPolicy, roots: PolicyRoots) -> list[str] | str:
    """Return the --add-dir flags the read/write rules imply, or why their per-root grants cannot be preserved."""
    if any(rule.capability in _PATH_CAPABILITIES for rule in policy.deny):
        return "read and write deny rules cannot be preserved"

    granted: list[set[str]] = []
    for capability in _PATH_CAPABILITIES:
        capability_roots = _granted_roots(policy, capability)
        if isinstance(capability_roots, str):
            return capability_roots

        granted.append(capability_roots)

    return _scratch_flags(granted, roots)


def _available_tools_flags(names: list[str]) -> list[str] | str:
    """Return the --available-tools flags for names, or an error for an empty list (an empty value exposes every tool)."""
    if not names:
        return "the policy grants no tools"

    return ["--available-tools", ",".join(names)]


def _tool_names(policy: ToolPolicy) -> list[str]:
    """Return the Copilot tool aliases the allow rules expose, in a fixed order."""
    granted = {rule.capability for rule in policy.allow}
    aliases = (
        (ToolCapability.READ, "read"),
        (ToolCapability.WRITE, "write"),
        (ToolCapability.SHELL, "shell"),
        (ToolCapability.NETWORK, "web_fetch"),
    )

    return [alias for capability, alias in aliases if capability in granted]


def _render_restricted(policy: ToolPolicy, roots: PolicyRoots) -> CopilotPolicyArgs | str:
    """Return the restrictive flags and isolated COPILOT_HOME, or why the policy cannot be preserved."""
    if roots.control is None:
        return "a restrictive policy needs a control directory for the isolated COPILOT_HOME"

    if any(rule.capability == ToolCapability.MCP for rule in policy_rules(policy)):
        return "mcp rules cannot be rendered for Copilot"

    available = _available_tools_flags(_tool_names(policy))
    scope = _path_scope(policy, roots)
    allows = _render_command_rules(
        [rule for rule in policy.allow if rule.capability not in _PATH_CAPABILITIES], deny=False
    )
    denies = _render_command_rules(policy.deny, deny=True)
    for rendered in (available, scope, allows, denies):
        if isinstance(rendered, str):
            return rendered

    grants_write = any(rule.capability == ToolCapability.WRITE for rule in policy.allow)
    flags = (
        "--no-ask-user",
        "--disallow-temp-dir",
        "--disable-builtin-mcps",
        *available,
        *scope,
        *(["--allow-tool", "write"] if grants_write else []),
        *allows,
        *denies,
    )

    return CopilotPolicyArgs(flags=flags, home=roots.control / "copilot-home")


def render_copilot_policy(policy: ToolPolicy, roots: PolicyRoots) -> CopilotPolicyArgs | str:
    """Render policy into Copilot CLI flags, or return why a rule cannot be preserved."""
    if policy.allow_all:
        return _render_unrestricted(policy)

    return _render_restricted(policy, roots)


def _render_for_request(
    policy: ToolPolicy, worktree_path: Path, invocation: AgentInvocationContext | None
) -> CopilotPolicyArgs | str:
    """Render the policy for one request, returning the first reason it is unsupported; an unrestricted policy needs no roots."""
    if policy.allow_all:
        return _render_unrestricted(policy)

    roots = resolve_policy_roots(policy, worktree_path, invocation)
    if isinstance(roots, str):
        return roots

    return render_copilot_policy(policy, roots)


def default_copilot_run(request: CliMutationRunRequest) -> CliMutationOutcome:
    """Invoke `gh copilot` with request.env plus the adapter-owned COPILOT_MODEL and map its JSONL stream into an outcome."""
    token = resolve_copilot_token()
    if token is None:
        return CliMutationOutcome(status="error", error_detail=missing_credential_error(COPILOT_PROVIDER_SPEC))

    rendered = _render_for_request(request.tools, request.worktree_path, request.invocation)
    if isinstance(rendered, str):
        return CliMutationOutcome(status="error", error_detail=tool_policy_unsupported_message("copilot"))

    # Keep prompt off argv to avoid OS argument length limits on large payloads.
    cmd = ["gh", "copilot", "--", "-p", "", "--output-format", "json", "--silent", *rendered.flags]
    env = dict(request.env)
    if rendered.home is not None:
        env["COPILOT_HOME"] = str(rendered.home)
    if request.model:
        env["COPILOT_MODEL"] = request.model

    try:
        completed = run_isolated_process(
            cmd,
            cwd=request.worktree_path,
            env=env,
            input_data=request.prompt.encode("utf-8"),
            timeout_seconds=request.timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return CliMutationOutcome(status="timeout")
    except FileNotFoundError as exc:
        return CliMutationOutcome(
            status="error",
            error_detail=(
                f"gh is not installed or not on PATH: {exc}. Fix: install the GitHub CLI (https://cli.github.com)"
            ),
        )
    except OSError as exc:
        return CliMutationOutcome(status="error", error_detail=str(exc))

    stdout_text = (completed.stdout or b"").decode("utf-8", errors="replace")
    stderr_text = (completed.stderr or b"").decode("utf-8", errors="replace")
    return _classify_copilot_output(int(completed.returncode), stdout_text, stderr_text)


class CopilotAgentAdapter(CliDirectMutationAdapter):
    """Run GitHub Copilot through the shared direct-mutation base."""

    def _provider_spec(self) -> ProviderSpec:
        """Return the Copilot descriptor."""
        return COPILOT_PROVIDER_SPEC

    def _provider_name(self) -> str:
        """Return the provider identifier string."""
        return "copilot"

    def _policy_unsupported(self, request: AgentRequest) -> str | None:
        """Return the unsupported message when Copilot cannot preserve the request's policy."""
        rendered = _render_for_request(request.tools, request.worktree_path, request.invocation)

        return tool_policy_unsupported_message("copilot") if isinstance(rendered, str) else None

    def _default_run(self, request: CliMutationRunRequest) -> CliMutationOutcome:
        """Execute Copilot CLI against mutation request."""
        return default_copilot_run(request)


COPILOT_PROVIDER_SPEC = ProviderSpec(
    token="copilot",
    credential_envs=COPILOT_TOKEN_ENVS,
    requires_model=False,
    supports_tool_policy=True,
    supports_os_sandbox=False,
    build=CopilotAgentAdapter,
    binary="gh",
    control_envs=COPILOT_CONTROL_ENVS,
)
