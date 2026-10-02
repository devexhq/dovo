"""Contract tests for the agent provider base class, descriptor registry, and resolved settings."""

from __future__ import annotations

import abc
import subprocess
import urllib.request

import pytest
from pydantic import ValidationError

from worktree.core.agents import (
    BaseAgentProvider,
    CopilotAgentAdapter,
    CursorAgentAdapter,
    GeminiAgentAdapter,
    LocalAgentAdapter,
    OllamaAgentAdapter,
    ProviderKind,
    ResolvedAgentSettings,
    get_agent_adapter,
)
from worktree.core.agents.registry import PROVIDERS


class BaseAgentProviderTests:
    def test_subclass_without_propose_fix_cannot_be_instantiated(self) -> None:
        """[tier-1/unit] BaseAgentProvider: a subclass that omits propose_fix raises TypeError on instantiation."""

        class _IncompleteProvider(BaseAgentProvider, abc.ABC):
            pass

        with pytest.raises(TypeError):
            _IncompleteProvider()  # pyright: ignore[reportAbstractUsage]  # intentional ill-typed input: the subject is the raised TypeError


class ProviderRegistryContractTests:
    def test_registered_tokens_match_initial_registry(self) -> None:
        """[tier-1/unit] PROVIDERS: keys equal {'local','ollama','cursor','gemini','copilot'} and every key equals its spec.token."""
        assert set(PROVIDERS) == {"local", "ollama", "cursor", "gemini", "copilot"}
        assert all(key == spec.token for key, spec in PROVIDERS.items())

    @pytest.mark.parametrize(
        ("token", "expected"),
        [
            pytest.param("local", (ProviderKind.DIFF_RETURNING, (), False, None), id="local"),
            pytest.param("ollama", (ProviderKind.DIFF_RETURNING, (), True, None), id="ollama"),
            pytest.param("cursor", (ProviderKind.DIRECT_MUTATION, ("CURSOR_API_KEY",), True, None), id="cursor"),
            pytest.param("gemini", (ProviderKind.DIRECT_MUTATION, ("GEMINI_API_KEY",), False, "gemini"), id="gemini"),
            pytest.param(
                "copilot", (ProviderKind.DIRECT_MUTATION, ("GH_TOKEN", "GITHUB_TOKEN"), False, "gh"), id="copilot"
            ),
        ],
    )
    def test_descriptor_matches_pre_determined_data(
        self, token: str, expected: tuple[ProviderKind, tuple[str, ...], bool, str | None]
    ) -> None:
        """[tier-1/unit] PROVIDERS[token]: kind, credential_envs, requires_model, and binary equal the issue's data (cursor requires a model) and both capability flags are False."""
        spec = PROVIDERS[token]

        assert (spec.kind, spec.credential_envs, spec.requires_model, spec.binary) == expected
        assert (spec.supports_tool_policy, spec.supports_os_sandbox) == (False, False)

    @pytest.mark.parametrize(
        ("token", "adapter_cls"),
        [
            pytest.param("local", LocalAgentAdapter, id="local"),
            pytest.param("ollama", OllamaAgentAdapter, id="ollama"),
            pytest.param("cursor", CursorAgentAdapter, id="cursor"),
            pytest.param("gemini", GeminiAgentAdapter, id="gemini"),
            pytest.param("copilot", CopilotAgentAdapter, id="copilot"),
        ],
    )
    def test_get_agent_adapter_builds_expected_adapter_without_side_effects(
        self, token: str, adapter_cls: type[BaseAgentProvider], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] get_agent_adapter: with credentials unset and process and network entry points patched to fail, the token returns an instance of exactly its adapter class."""
        for name in ("CURSOR_API_KEY", "GEMINI_API_KEY", "GH_TOKEN", "GITHUB_TOKEN"):
            monkeypatch.delenv(name, raising=False)

        def _forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("adapter construction must not start a process or contact an endpoint")

        monkeypatch.setattr(subprocess, "Popen", _forbidden)
        monkeypatch.setattr(urllib.request, "urlopen", _forbidden)

        assert type(get_agent_adapter(token)) is adapter_cls

    def test_unregistered_token_raises_classified_value_error(self) -> None:
        """[tier-1/unit] get_agent_adapter: 'openai' raises ValueError carrying the AGENT_PROVIDER_UNSUPPORTED message with the sorted registered tokens."""
        with pytest.raises(ValueError) as exc:
            get_agent_adapter("openai")

        assert str(exc.value) == (
            "Unsupported agent provider 'openai' (AGENT_PROVIDER_UNSUPPORTED). "
            "Supported: copilot, cursor, gemini, local, ollama."
        )


class ResolvedAgentSettingsTests:
    @pytest.mark.parametrize(
        "overrides",
        [{"temperature": -0.1}, {"temperature": 2.1}, {"max_tokens": 0}],
    )
    def test_out_of_range_values_are_rejected_and_instance_is_frozen(self, overrides: dict[str, float | int]) -> None:
        """[tier-1/unit] ResolvedAgentSettings: temperature outside [0, 2] or max_tokens below 1 raises ValidationError, and assigning a field on a valid instance raises ValidationError."""
        valid = {"provider": "local", "model": None, "endpoint": None, "temperature": 0.2, "max_tokens": 4096}

        with pytest.raises(ValidationError):
            ResolvedAgentSettings.model_validate({**valid, **overrides})

        settings = ResolvedAgentSettings.model_validate(valid)
        with pytest.raises(ValidationError):
            settings.provider = "ollama"
