"""Contract tests for the agent provider base class, descriptor registry, and resolved settings."""

from __future__ import annotations

import os
import subprocess
import urllib.request
from collections.abc import Iterator, MutableMapping

import pytest
from pydantic import ValidationError

from dovo.core.agents import CopilotAgentAdapter, ResolvedAgentSettings, get_agent_adapter
from dovo.core.agents.registry import PROVIDERS


class _UnreadableEnvironment(MutableMapping[str, str]):
    """Environment stand-in that fails the test on any read; writes are swallowed so pytest's own bookkeeping works."""

    def __getitem__(self, key: str) -> str:
        raise AssertionError(f"environment read of {key!r}")

    def __setitem__(self, key: str, value: str) -> None:
        pass

    def __delitem__(self, key: str) -> None:
        pass

    def __iter__(self) -> Iterator[str]:
        raise AssertionError("environment iteration")

    def __len__(self) -> int:
        raise AssertionError("environment length read")


class ProviderRegistryContractTests:
    def test_descriptor_inspection_and_adapter_construction_read_no_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] PROVIDERS / get_agent_adapter: with os.environ replaced by a mapping that raises AssertionError on any read, iterating PROVIDERS credential_envs and building get_agent_adapter("copilot") completes without raising."""
        monkeypatch.setattr(os, "environ", _UnreadableEnvironment())

        envs = {token: spec.credential_envs for token, spec in PROVIDERS.items()}
        adapter = get_agent_adapter("copilot")

        assert envs == {"copilot": ("GH_TOKEN", "GITHUB_TOKEN")}
        assert type(adapter) is CopilotAgentAdapter

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
