"""In-process Ollama HTTP agent adapter."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ValidationError

from dovo.core.agents.base import BaseAgentProvider
from dovo.core.agents.models import AgentRequest, AgentResponse, AgentResponseStatus

DEFAULT_OLLAMA_ENDPOINT = "http://localhost:11434"
OLLAMA_HOST_ENV = "OLLAMA_HOST"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_TOKENS = 4096
MODEL_OUTPUT_UNPARSEABLE = "model_output_unparseable"

_FENCE_RE = re.compile(
    r"```(?:json)?\s*([\s\S]*?)\s*```",
    re.IGNORECASE,
)


class OllamaModelStdout(BaseModel):
    """JSON object the Ollama model is instructed to return."""

    model_config = {"extra": "ignore", "strict": True}

    unfixable: bool = False
    unfixable_reason: str | None = None
    unified_diff: str | None = None
    summary: str | None = None


def _is_timeout_reason(reason: object) -> bool:
    """Determine whether a failure reason text indicates a timeout."""
    text = str(reason).lower()
    return "timed out" in text or "timeout" in text


def resolve_ollama_endpoint(
    request_endpoint: str | None,
    *,
    env: dict[str, str] | None = None,
) -> str:
    """Resolve Ollama base URL from request, env, or default."""
    environ = env if env is not None else os.environ
    if request_endpoint is not None and str(request_endpoint).strip():
        raw = str(request_endpoint).strip()
    else:
        host = environ.get(OLLAMA_HOST_ENV)
        if host is not None and host.strip():
            raw = host.strip()
        else:
            raw = DEFAULT_OLLAMA_ENDPOINT
    return raw.rstrip("/")


def validate_ollama_endpoint(base: str) -> str | None:
    """Return an error detail when ``base`` is not an absolute http(s) URL."""
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return f"invalid Ollama endpoint '{base}': must be an absolute http:// or https:// URL"
    return None


_DIRECT_GUIDANCE = (
    "Carry out the instruction. Return a unified_diff only when file changes are required; "
    "otherwise set unified_diff to null and put your findings in summary, "
    "or set unfixable=true with a short reason if you cannot."
)
_REMEDIATION_GUIDANCE = (
    "Propose the smallest correct unified_diff that fixes the failure, "
    "or set unfixable=true with a short reason if you cannot."
)


def build_ollama_messages(request: AgentRequest) -> list[dict[str, str]]:
    """Build system/user chat messages for the Ollama chat API."""
    system = (
        "You are a coding agent that returns changes as unified diffs.\n"
        "Reply with ONLY one JSON object (no markdown, no prose) using exactly "
        "these fields:\n"
        '- "unfixable": boolean\n'
        '- "unfixable_reason": string or null\n'
        '- "unified_diff": string or null '
        "(git-style unified diff with diff --git a/... b/... paths relative "
        "to the sandbox; null when no file change is needed)\n"
        '- "summary": string or null (your findings, plan, or review text)\n'
        "Do not apply patches yourself. Do not wrap the JSON in code fences."
    )
    user_obj: dict[str, object] = {
        "mode": request.mode,
        "sandbox_path": str(request.sandbox_path),
        "instruction": request.instruction,
    }
    if request.payload is not None:
        user_obj["payload"] = request.payload.model_dump(mode="json")
    user_obj["guidance"] = _DIRECT_GUIDANCE if request.mode == "direct" else _REMEDIATION_GUIDANCE
    user = json.dumps(user_obj, indent=2, ensure_ascii=False)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# complexipy: ignore (Revisit when we flesh out agents)
def extract_json_object(text: str) -> str | None:
    """Return the first JSON object substring from model text, if any."""
    stripped = text.strip()
    if not stripped:
        return None
    fence = _FENCE_RE.search(stripped)
    if fence:
        stripped = fence.group(1).strip()
    start = stripped.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(stripped)):
        ch = stripped[index]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return stripped[start : index + 1]
    return None


def parse_ollama_model_text(raw_text: str) -> OllamaModelStdout | None:
    """Parse model text into stdout fields; None when unparseable."""
    blob = extract_json_object(raw_text)
    if blob is None:
        return None
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return OllamaModelStdout.model_validate(data)
    except ValidationError:
        return None


def map_ollama_stdout(parsed: OllamaModelStdout, *, raw_text: str, duration_ms: int) -> AgentResponse:
    """Map parsed model JSON to an AgentResponse."""
    if parsed.unfixable:
        return AgentResponse(
            status=AgentResponseStatus.UNFIXABLE,
            unified_diff=None,
            summary=parsed.summary,
            unfixable_reason=parsed.unfixable_reason,
            raw_text=raw_text,
            duration_ms=duration_ms,
            errors=[],
        )
    diff = parsed.unified_diff
    if diff is not None and diff != "":
        return AgentResponse(
            status=AgentResponseStatus.PROPOSED_PATCH,
            unified_diff=diff,
            summary=parsed.summary,
            unfixable_reason=None,
            raw_text=raw_text,
            duration_ms=duration_ms,
            errors=[],
        )
    return AgentResponse(
        status=AgentResponseStatus.NO_OP,
        unified_diff=diff if diff == "" else None,
        summary=parsed.summary,
        unfixable_reason=None,
        raw_text=raw_text,
        duration_ms=duration_ms,
        errors=[],
    )


def default_http_post(url: str, body: bytes, timeout_seconds: float) -> tuple[int, str]:
    """POST JSON bytes and return (status_code, response_text)."""
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            status = int(getattr(resp, "status", 200) or 200)
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        err_body = exc.read() if hasattr(exc, "read") else b""
        text = err_body.decode("utf-8", errors="replace") if err_body else ""
        return int(exc.code), text
    return status, raw.decode("utf-8", errors="replace")


def _chat_content_from_response(data: dict[str, Any]) -> str | None:
    """Extract chat message text from Ollama JSON response dictionary."""
    message = data.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
    # Some gateways put text at top-level response
    response = data.get("response")
    if isinstance(response, str):
        return response
    return None


class OllamaAgentAdapter(BaseAgentProvider):
    """Call a local Ollama server and map chat output to AgentResponse."""

    # complexipy: ignore (Revisit when we flesh out agents)
    def propose_fix(self, request: AgentRequest) -> AgentResponse:
        """Request a fix from Ollama; never raises for classified outcomes."""
        started = time.monotonic()

        model = request.model.strip() if request.model else ""
        if not model:
            duration_ms = int((time.monotonic() - started) * 1000)
            return AgentResponse(
                status=AgentResponseStatus.PROVIDER_ERROR,
                duration_ms=duration_ms,
                errors=[
                    "Agent provider error (AGENT_PROVIDER_ERROR): "
                    "ollama requires a non-empty model. "
                    "Fix: set agent.model in .dovo/config.json"
                ],
            )

        base = resolve_ollama_endpoint(request.endpoint)
        endpoint_error = validate_ollama_endpoint(base)
        if endpoint_error is not None:
            duration_ms = int((time.monotonic() - started) * 1000)
            return AgentResponse(
                status=AgentResponseStatus.PROVIDER_ERROR,
                duration_ms=duration_ms,
                errors=[f"Agent provider error (AGENT_PROVIDER_ERROR): {endpoint_error}"],
            )

        temperature = float(request.temperature) if request.temperature is not None else DEFAULT_TEMPERATURE
        max_tokens = int(request.max_tokens) if request.max_tokens is not None else DEFAULT_MAX_TOKENS
        timeout_seconds = float(request.timeout_seconds)
        url = f"{base}/api/chat"
        body_obj = {
            "model": model,
            "stream": False,
            "messages": build_ollama_messages(request),
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        body = json.dumps(body_obj, separators=(",", ":")).encode("utf-8")

        try:
            status, text = default_http_post(url, body, timeout_seconds)
        except TimeoutError:
            duration_ms = int((time.monotonic() - started) * 1000)
            return AgentResponse(
                status=AgentResponseStatus.TIMEOUT,
                duration_ms=duration_ms,
                errors=[
                    f"Agent timed out after {request.timeout_seconds}s "
                    f"(provider=ollama).\n"
                    "Fix:\n"
                    "- raise agent.timeout_seconds on the blueprint"
                ],
            )
        except urllib.error.URLError as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError) or _is_timeout_reason(reason):
                return AgentResponse(
                    status=AgentResponseStatus.TIMEOUT,
                    duration_ms=duration_ms,
                    errors=[
                        f"Agent timed out after {request.timeout_seconds}s "
                        f"(provider=ollama).\n"
                        "Fix:\n"
                        "- raise agent.timeout_seconds on the blueprint"
                    ],
                )
            return AgentResponse(
                status=AgentResponseStatus.PROVIDER_ERROR,
                duration_ms=duration_ms,
                errors=[f"Agent provider error (AGENT_PROVIDER_ERROR): failed to reach Ollama at '{base}': {reason}"],
            )
        except OSError as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            if isinstance(exc, TimeoutError) or _is_timeout_reason(exc):
                return AgentResponse(
                    status=AgentResponseStatus.TIMEOUT,
                    duration_ms=duration_ms,
                    errors=[
                        f"Agent timed out after {request.timeout_seconds}s "
                        f"(provider=ollama).\n"
                        "Fix:\n"
                        "- raise agent.timeout_seconds on the blueprint"
                    ],
                )
            return AgentResponse(
                status=AgentResponseStatus.PROVIDER_ERROR,
                duration_ms=duration_ms,
                errors=[f"Agent provider error (AGENT_PROVIDER_ERROR): failed to reach Ollama at '{base}': {exc}"],
            )

        duration_ms = int((time.monotonic() - started) * 1000)
        if status < 200 or status >= 300:
            snippet = text.strip().replace("\n", " ")[:300]
            detail = f"Ollama HTTP {status} from '{url}'"
            if snippet:
                detail = f"{detail}: {snippet}"
            return AgentResponse(
                status=AgentResponseStatus.PROVIDER_ERROR,
                raw_text=text or None,
                duration_ms=duration_ms,
                errors=[f"Agent provider error (AGENT_PROVIDER_ERROR): {detail}"],
            )

        try:
            data = json.loads(text) if text.strip() else None
        except json.JSONDecodeError as exc:
            return AgentResponse(
                status=AgentResponseStatus.PROVIDER_ERROR,
                raw_text=text or None,
                duration_ms=duration_ms,
                errors=[f"Agent provider error (AGENT_PROVIDER_ERROR): invalid JSON from Ollama chat API: {exc}"],
            )
        if not isinstance(data, dict):
            return AgentResponse(
                status=AgentResponseStatus.PROVIDER_ERROR,
                raw_text=text or None,
                duration_ms=duration_ms,
                errors=["Agent provider error (AGENT_PROVIDER_ERROR): Ollama chat API returned a non-object"],
            )

        content = _chat_content_from_response(data)
        if content is None:
            return AgentResponse(
                status=AgentResponseStatus.PROVIDER_ERROR,
                raw_text=text or None,
                duration_ms=duration_ms,
                errors=[
                    "Agent provider error (AGENT_PROVIDER_ERROR): Ollama chat API response missing message.content"
                ],
            )

        parsed = parse_ollama_model_text(content)
        if parsed is None:
            return AgentResponse(
                status=AgentResponseStatus.UNFIXABLE,
                summary=None,
                unfixable_reason=MODEL_OUTPUT_UNPARSEABLE,
                raw_text=content,
                duration_ms=duration_ms,
                errors=[],
            )
        return map_ollama_stdout(parsed, raw_text=content, duration_ms=duration_ms)
