"""Tests for the in-process Ollama HTTP agent adapter."""

from __future__ import annotations

import email.message
import io
import json
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from dovo.core.agents import (
    AgentFailurePayload,
    AgentRequest,
    AgentResponseStatus,
    OllamaAgentAdapter,
)
from dovo.core.agents.ollama import (
    DEFAULT_OLLAMA_ENDPOINT,
    MODEL_OUTPUT_UNPARSEABLE,
    OLLAMA_HOST_ENV,
    OllamaModelStdout,
    build_ollama_messages,
    default_http_post,
    extract_json_object,
    parse_ollama_model_text,
    resolve_ollama_endpoint,
    validate_ollama_endpoint,
)
from tests.harness import AgentRequestBuilder


def _chat_body(content: str) -> str:
    return json.dumps({"message": {"role": "assistant", "content": content}})


def _ollama_request(
    worktree_path: Path,
    *,
    model: str | None = "smollm2:1.7b",
    endpoint: str | None = "http://127.0.0.1:11434",
) -> AgentRequest:
    """Build an AgentRequest carrying the model/endpoint/temperature/max_tokens fields Ollama reads."""
    builder = AgentRequestBuilder().with_worktree_path(worktree_path).with_temperature(0.2).with_max_tokens(1024)
    if model is not None:
        builder = builder.with_model(model)
    if endpoint is not None:
        builder = builder.with_endpoint(endpoint)
    return builder.build()


class DefaultHttpPostTests:
    """Direct tests for the default_http_post HTTP boundary helper."""

    def test_http_post_success_returns_status_and_body(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A 2xx response returns its status code and decoded body."""

        class _MockResp:
            status = 200

            def __enter__(self) -> _MockResp:
                return self

            def __exit__(self, *args: object) -> None:
                pass

            def read(self) -> bytes:
                return b'{"ok": true}'

        monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: _MockResp())

        status, text = default_http_post("http://localhost:11434/api/chat", b"{}", 5.0)

        assert (status, text) == (200, '{"ok": true}')

    @pytest.mark.parametrize(
        ("code", "reason", "fp", "expected_text"),
        [
            pytest.param(404, "Not Found", io.BytesIO(b"model not found"), "model not found", id="with_body"),
            pytest.param(500, "Internal Server Error", None, "", id="without_body"),
        ],
    )
    def test_http_post_handles_http_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        code: int,
        reason: str,
        fp: io.BytesIO | None,
        expected_text: str,
    ) -> None:
        """An HTTPError returns its code and body when readable, or an empty string when it is not."""
        err = HTTPError("http://localhost:11434/api/chat", code, reason, email.message.Message(), fp)

        def _urlopen(req: urllib.request.Request, timeout: float) -> object:
            raise err

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)

        status, text = default_http_post("http://localhost:11434/api/chat", b"{}", 5.0)

        assert (status, text) == (code, expected_text)


class ResolveOllamaEndpointTests:
    @pytest.mark.parametrize(
        ("request_endpoint", "env", "expected"),
        [
            pytest.param("http://example:11434/", {}, "http://example:11434", id="request_endpoint_wins"),
            pytest.param(None, {OLLAMA_HOST_ENV: "http://host:1"}, "http://host:1", id="env_fallback_when_unset"),
            pytest.param(None, {}, DEFAULT_OLLAMA_ENDPOINT, id="default_when_request_and_env_unset"),
        ],
    )
    def test_resolve_prefers_request_then_env_then_default(
        self, request_endpoint: str | None, env: dict[str, str], expected: str
    ) -> None:
        """Endpoint resolution order is: request field, then OLLAMA_HOST env var, then the built-in default."""
        assert resolve_ollama_endpoint(request_endpoint, env=env) == expected


class ValidateOllamaEndpointTests:
    @pytest.mark.parametrize(
        ("base", "expected_error"),
        [
            pytest.param("http://127.0.0.1:11434", None, id="http_scheme_is_valid"),
            pytest.param("https://x", None, id="https_scheme_is_valid"),
            pytest.param(
                "127.0.0.1:11434",
                "invalid Ollama endpoint '127.0.0.1:11434': must be an absolute http:// or https:// URL",
                id="missing_scheme_is_invalid",
            ),
            pytest.param(
                "ftp://x",
                "invalid Ollama endpoint 'ftp://x': must be an absolute http:// or https:// URL",
                id="unsupported_scheme_is_invalid",
            ),
        ],
    )
    def test_validate_accepts_http_schemes_rejects_others(self, base: str, expected_error: str | None) -> None:
        """Only an absolute http:// or https:// URL validates; anything else returns the exact error detail."""
        assert validate_ollama_endpoint(base) == expected_error


class ExtractJsonObjectTests:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            pytest.param(
                'Here:\n```json\n{"unified_diff": "d", "unfixable": false}\n```\n',
                '{"unified_diff": "d", "unfixable": false}',
                id="fenced",
            ),
            pytest.param('prose before {"a": 1} prose after', '{"a": 1}', id="bare_inline"),
        ],
    )
    def test_extract_finds_balanced_object_fenced_or_bare(self, text: str, expected: str) -> None:
        """The first balanced JSON object is extracted whether it is fenced in ```json or bare in prose."""
        assert extract_json_object(text) == expected


class ParseOllamaModelTextTests:
    def test_parse_ignores_undeclared_keys(self) -> None:
        """Extra JSON keys the model schema doesn't declare are ignored, not rejected."""
        parsed = parse_ollama_model_text('{"unified_diff": "diff", "summary": "ok", "extra": 1}')

        assert parsed == OllamaModelStdout(unfixable=False, unfixable_reason=None, unified_diff="diff", summary="ok")


class BuildOllamaMessagesTests:
    def test_direct_request_messages_omit_payload_and_use_direct_guidance(self, tmp_path: Path) -> None:
        """[tier-1/unit] build_ollama_messages: a direct request yields [system, user] whose user JSON equals {mode, worktree_path, instruction, guidance(direct)} with no payload key."""
        request = AgentRequest(mode="direct", instruction="Plan the change", worktree_path=tmp_path, timeout_seconds=10)

        messages = build_ollama_messages(request)

        assert [message["role"] for message in messages] == ["system", "user"]
        assert json.loads(messages[1]["content"]) == {
            "mode": "direct",
            "worktree_path": str(tmp_path),
            "instruction": "Plan the change",
            "guidance": (
                "Carry out the instruction. Return a unified_diff only when file changes are required; "
                "otherwise set unified_diff to null and put your findings in summary, "
                "or set unfixable=true with a short reason if you cannot."
            ),
        }

    def test_remediation_request_messages_embed_payload_and_repair_guidance(self, tmp_path: Path) -> None:
        """[tier-1/unit] build_ollama_messages: a fix_failure request yields user JSON {mode, worktree_path, instruction, payload, guidance(remediation)}."""
        request = AgentRequestBuilder().with_worktree_path(tmp_path).build()

        messages = build_ollama_messages(request)

        assert [message["role"] for message in messages] == ["system", "user"]
        assert json.loads(messages[1]["content"]) == {
            "mode": "fix_failure",
            "worktree_path": str(tmp_path),
            "instruction": "Fix the failing test.",
            "payload": AgentFailurePayload(
                command="pytest",
                args=["-q"],
                trigger_status="failed",
                exit_code=1,
                timed_out=False,
                duration_ms=10,
                stdout="boom",
                stderr="",
            ).model_dump(mode="json"),
            "guidance": (
                "Propose the smallest correct unified_diff that fixes the failure, "
                "or set unfixable=true with a short reason if you cannot."
            ),
        }


class OllamaAdapterTests:
    def test_valid_json_diff_returns_proposed_patch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A well-formed chat response with a non-empty diff proposes a patch, and the chat request is exact."""
        content = json.dumps({"unified_diff": "diff --git a/x b/x\n", "summary": "fixed"})
        request = _ollama_request(tmp_path)

        def http_post(url: str, body: bytes, timeout: float) -> tuple[int, str]:
            assert url == "http://127.0.0.1:11434/api/chat"
            assert timeout == 10.0
            payload = json.loads(body.decode("utf-8"))
            assert payload == {
                "model": "smollm2:1.7b",
                "stream": False,
                "messages": build_ollama_messages(request),
                "options": {"temperature": 0.2, "num_predict": 1024},
            }
            return 200, _chat_body(content)

        monkeypatch.setattr("dovo.core.agents.ollama.default_http_post", http_post)

        resp = OllamaAgentAdapter().propose_fix(request)

        assert resp.status == AgentResponseStatus.PROPOSED_PATCH
        assert resp.unified_diff == "diff --git a/x b/x\n"
        assert resp.summary == "fixed"
        assert resp.raw_text == content

    def test_unfixable_flag_returns_unfixable_status(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A model response declaring unfixable=true maps to UNFIXABLE with its reason and summary."""
        content = json.dumps({"unfixable": True, "unfixable_reason": "needs redesign", "summary": "nope"})
        monkeypatch.setattr(
            "dovo.core.agents.ollama.default_http_post", lambda *args, **kwargs: (200, _chat_body(content))
        )

        resp = OllamaAgentAdapter().propose_fix(_ollama_request(tmp_path))

        assert resp.status == AgentResponseStatus.UNFIXABLE
        assert resp.summary == "nope"
        assert resp.unfixable_reason == "needs redesign"
        assert resp.raw_text == content

    def test_empty_diff_returns_no_op_status(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """An explicit empty unified_diff maps to NO_OP, preserving the empty string rather than None."""
        content = json.dumps({"unified_diff": "", "summary": "nothing"})
        monkeypatch.setattr(
            "dovo.core.agents.ollama.default_http_post", lambda *args, **kwargs: (200, _chat_body(content))
        )

        resp = OllamaAgentAdapter().propose_fix(_ollama_request(tmp_path))

        assert resp.status == AgentResponseStatus.NO_OP
        assert resp.unified_diff == ""
        assert resp.summary == "nothing"
        assert resp.raw_text == content

    def test_fenced_json_response_returns_proposed_patch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Model output wrapped in a ```json fence is unwrapped before parsing, and raw_text keeps the fence."""
        content = '```json\n{"unified_diff": "d\\n", "summary": "x"}\n```'
        monkeypatch.setattr(
            "dovo.core.agents.ollama.default_http_post", lambda *args, **kwargs: (200, _chat_body(content))
        )

        resp = OllamaAgentAdapter().propose_fix(_ollama_request(tmp_path))

        assert resp.status == AgentResponseStatus.PROPOSED_PATCH
        assert resp.unified_diff == "d\n"
        assert resp.summary == "x"
        assert resp.raw_text == content

    def test_unparseable_model_text_returns_unfixable(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Model text with no extractable JSON object maps to UNFIXABLE with the unparseable-output reason."""
        monkeypatch.setattr(
            "dovo.core.agents.ollama.default_http_post",
            lambda *args, **kwargs: (200, _chat_body("sorry I cannot produce JSON today")),
        )

        resp = OllamaAgentAdapter().propose_fix(_ollama_request(tmp_path))

        assert resp.status == AgentResponseStatus.UNFIXABLE
        assert resp.unfixable_reason == MODEL_OUTPUT_UNPARSEABLE
        assert resp.raw_text == "sorry I cannot produce JSON today"

    def test_missing_model_returns_provider_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A blank model fails before any HTTP call is made."""

        def _unreachable(url: str, body: bytes, timeout: float) -> tuple[int, str]:
            return pytest.fail("must not be called when model is missing")

        monkeypatch.setattr("dovo.core.agents.ollama.default_http_post", _unreachable)

        resp = OllamaAgentAdapter().propose_fix(_ollama_request(tmp_path, model=None))

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.errors == [
            "Agent provider error (AGENT_PROVIDER_ERROR): "
            "ollama requires a non-empty model. Fix: set agent.model in .dovo/config.json"
        ]

    def test_invalid_endpoint_scheme_returns_provider_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An endpoint missing an http(s) scheme fails before any HTTP call is made."""

        def _unreachable(url: str, body: bytes, timeout: float) -> tuple[int, str]:
            return pytest.fail("must not be called when the endpoint is invalid")

        monkeypatch.setattr("dovo.core.agents.ollama.default_http_post", _unreachable)

        resp = OllamaAgentAdapter().propose_fix(_ollama_request(tmp_path, endpoint="127.0.0.1:11434"))

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.errors == [
            "Agent provider error (AGENT_PROVIDER_ERROR): "
            "invalid Ollama endpoint '127.0.0.1:11434': must be an absolute http:// or https:// URL"
        ]

    @pytest.mark.parametrize(
        "reason",
        [
            pytest.param("connection refused", id="connection_refused"),
            pytest.param("connection reset", id="connection_reset"),
        ],
    )
    def test_non_timeout_url_error_returns_provider_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str
    ) -> None:
        """A non-timeout URLError from the HTTP boundary maps to PROVIDER_ERROR naming the endpoint and reason."""

        def http_post(url: str, body: bytes, timeout: float) -> tuple[int, str]:
            raise URLError(reason)

        monkeypatch.setattr("dovo.core.agents.ollama.default_http_post", http_post)

        resp = OllamaAgentAdapter().propose_fix(_ollama_request(tmp_path))

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.errors == [
            f"Agent provider error (AGENT_PROVIDER_ERROR): failed to reach Ollama at 'http://127.0.0.1:11434': {reason}"
        ]

    def test_http_500_status_returns_provider_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A non-2xx HTTP status maps to PROVIDER_ERROR naming the status and response snippet."""
        monkeypatch.setattr("dovo.core.agents.ollama.default_http_post", lambda *args, **kwargs: (500, "internal boom"))

        resp = OllamaAgentAdapter().propose_fix(_ollama_request(tmp_path))

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.raw_text == "internal boom"
        assert resp.errors == [
            "Agent provider error (AGENT_PROVIDER_ERROR): "
            "Ollama HTTP 500 from 'http://127.0.0.1:11434/api/chat': internal boom"
        ]

    def test_http_post_timeout_returns_timeout_status(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A TimeoutError from the HTTP boundary maps to TIMEOUT, not PROVIDER_ERROR."""

        def http_post(url: str, body: bytes, timeout: float) -> tuple[int, str]:
            raise TimeoutError("timed out")

        monkeypatch.setattr("dovo.core.agents.ollama.default_http_post", http_post)

        resp = OllamaAgentAdapter().propose_fix(_ollama_request(tmp_path))

        assert resp.status == AgentResponseStatus.TIMEOUT
        assert resp.errors == [
            "Agent timed out after 10s (provider=ollama).\nFix:\n- raise agent.timeout_seconds on the blueprint"
        ]

    def test_non_object_json_body_returns_provider_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A JSON array or scalar chat body maps to PROVIDER_ERROR rather than crashing on attribute access."""
        monkeypatch.setattr("dovo.core.agents.ollama.default_http_post", lambda *args, **kwargs: (200, "[1, 2, 3]"))

        resp = OllamaAgentAdapter().propose_fix(_ollama_request(tmp_path))

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.raw_text == "[1, 2, 3]"
        assert resp.errors == ["Agent provider error (AGENT_PROVIDER_ERROR): Ollama chat API returned a non-object"]

    def test_missing_message_content_returns_provider_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A chat body whose message omits content maps to PROVIDER_ERROR."""
        body = '{"message": {"role": "assistant"}}'
        monkeypatch.setattr("dovo.core.agents.ollama.default_http_post", lambda *args, **kwargs: (200, body))

        resp = OllamaAgentAdapter().propose_fix(_ollama_request(tmp_path))

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.raw_text == body
        assert resp.errors == [
            "Agent provider error (AGENT_PROVIDER_ERROR): Ollama chat API response missing message.content"
        ]

    def test_invalid_json_http_body_returns_provider_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Malformed JSON in the chat body maps to PROVIDER_ERROR carrying the decode error detail."""
        monkeypatch.setattr(
            "dovo.core.agents.ollama.default_http_post", lambda *args, **kwargs: (200, "invalid json {")
        )

        resp = OllamaAgentAdapter().propose_fix(_ollama_request(tmp_path))

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.raw_text == "invalid json {"
        assert resp.errors == [
            "Agent provider error (AGENT_PROVIDER_ERROR): "
            "invalid JSON from Ollama chat API: Expecting value: line 1 column 1 (char 0)"
        ]
