"""Contract tests for the agent provider base class, descriptor registry, and resolved settings."""

from __future__ import annotations

import abc
import subprocess
import urllib.request

import pytest
from pydantic import ValidationError

from dovo.core.agents import BaseAgentProvider, CopilotAgentAdapter, ResolvedAgentSettings, get_agent_adapter
from dovo.core.agents.registry import PROVIDERS


class BaseAgentProviderTests:
    def test_subclass_without_propose_fix_cannot_be_instantiated(self) -> None:
        """[tier-1/unit] BaseAgentProvider: a subclass that omits propose_fix raises TypeError on instantiation."""

        class _IncompleteProvider(BaseAgentProvider, abc.ABC):
            pass

        with pytest.raises(TypeError):
            _IncompleteProvider()  # pyright: ignore[reportAbstractUsage]  # intentional ill-typed input: the subject is the raised TypeError


class ProviderRegistryContractTests:
    def test_registered_tokens_match_initial_registry(self) -> None:
        """[tier-1/unit] PROVIDERS: keys equal {'copilot'} and every key equals its spec.token."""
        assert set(PROVIDERS) == {"copilot"}
        assert all(key == spec.token for key, spec in PROVIDERS.items())

    def test_copilot_descriptor_matches_pre_determined_data(self) -> None:
        """[tier-1/unit] PROVIDERS['copilot']: credential_envs ('GH_TOKEN','GITHUB_TOKEN'), requires_model False, binary 'gh', supports_tool_policy False, supports_os_sandbox False."""
        spec = PROVIDERS["copilot"]

        assert (spec.credential_envs, spec.requires_model, spec.binary) == (("GH_TOKEN", "GITHUB_TOKEN"), False, "gh")
        assert (spec.supports_tool_policy, spec.supports_os_sandbox) == (False, False)

    def test_get_agent_adapter_builds_copilot_adapter_without_side_effects(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] get_agent_adapter: with GH_TOKEN/GITHUB_TOKEN unset and Popen and urlopen patched to fail, 'copilot' returns an instance of exactly CopilotAgentAdapter."""
        for name in ("GH_TOKEN", "GITHUB_TOKEN"):
            monkeypatch.delenv(name, raising=False)

        def _forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("adapter construction must not start a process or contact an endpoint")

        monkeypatch.setattr(subprocess, "Popen", _forbidden)
        monkeypatch.setattr(urllib.request, "urlopen", _forbidden)

        assert type(get_agent_adapter("copilot")) is CopilotAgentAdapter

    def test_unregistered_token_raises_classified_value_error(self) -> None:
        """[tier-1/unit] get_agent_adapter: 'unregistered' raises ValueError whose message is "Unsupported agent provider 'unregistered' (AGENT_PROVIDER_UNSUPPORTED). Supported: copilot." and builds no adapter."""
        with pytest.raises(ValueError) as exc:
            get_agent_adapter("unregistered")

        assert (
            str(exc.value)
            == "Unsupported agent provider 'unregistered' (AGENT_PROVIDER_UNSUPPORTED). Supported: copilot."
        )


class ResolvedAgentSettingsTests:
    @pytest.mark.parametrize(
        "overrides",
        [{"temperature": -0.1}, {"temperature": 2.1}, {"max_tokens": 0}],
    )
    def test_out_of_range_values_are_rejected_and_instance_is_frozen(self, overrides: dict[str, float | int]) -> None:
        """[tier-1/unit] ResolvedAgentSettings: temperature outside [0, 2] or max_tokens below 1 raises ValidationError, and assigning a field on a valid instance raises ValidationError."""
        valid = {"provider": "copilot", "model": None, "endpoint": None, "temperature": 0.2, "max_tokens": 4096}

        with pytest.raises(ValidationError):
            ResolvedAgentSettings.model_validate({**valid, **overrides})

        settings = ResolvedAgentSettings.model_validate(valid)
        with pytest.raises(ValidationError):
            settings.provider = "copilot"
