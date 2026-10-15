"""Contract tests for the agent provider base class, descriptor registry, and resolved settings."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from collections.abc import Iterator, MutableMapping
from pathlib import Path

import pytest
from pydantic import ValidationError

from dovo.core.agents import (
    ClaudeAgentAdapter,
    CopilotAgentAdapter,
    ResolvedAgentSettings,
    default_tool_policy,
    get_agent_adapter,
)
from dovo.core.agents.cli_mutation import CliDirectMutationAdapter, CliMutationOutcome, CliMutationRunRequest
from dovo.core.agents.registry import PROVIDERS
from tests.harness import AgentRequestBuilder, FakeAgentRunner


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
        monkeypatch.setattr(os, "environ", _UnreadableEnvironment())

        envs = {token: spec.credential_envs for token, spec in PROVIDERS.items()}
        adapter_types = {token: type(get_agent_adapter(token)) for token in PROVIDERS}

        assert envs == {
            "claude": ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"),
            "copilot": ("GH_TOKEN", "GITHUB_TOKEN"),
        }
        assert adapter_types == {"claude": ClaudeAgentAdapter, "copilot": CopilotAgentAdapter}

    def test_registered_tokens_match_registry(self) -> None:
        assert set(PROVIDERS) == {"claude", "copilot"}
        assert all(key == spec.token for key, spec in PROVIDERS.items())

    def test_copilot_descriptor_matches_pre_determined_data(self) -> None:
        spec = PROVIDERS["copilot"]

        assert (spec.credential_envs, spec.requires_model, spec.binary) == (("GH_TOKEN", "GITHUB_TOKEN"), False, "gh")
        assert (spec.supports_tool_policy, spec.supports_os_sandbox) == (True, False)

    @pytest.mark.parametrize(
        ("token", "adapter_type"),
        [
            pytest.param("claude", ClaudeAgentAdapter, id="claude"),
            pytest.param("copilot", CopilotAgentAdapter, id="copilot"),
        ],
    )
    def test_get_agent_adapter_builds_exact_adapter_without_side_effects(
        self, token: str, adapter_type: type, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in PROVIDERS[token].credential_envs:
            monkeypatch.delenv(name, raising=False)

        def _forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("adapter construction must not start a process or contact an endpoint")

        monkeypatch.setattr(subprocess, "Popen", _forbidden)
        monkeypatch.setattr(urllib.request, "urlopen", _forbidden)

        assert type(get_agent_adapter(token)) is adapter_type

    def test_unregistered_token_raises_classified_value_error(self) -> None:
        with pytest.raises(ValueError) as exc:
            get_agent_adapter("unregistered")

        assert (
            str(exc.value)
            == "Unsupported agent provider 'unregistered' (AGENT_PROVIDER_UNSUPPORTED). Supported: claude, copilot."
        )


class ResolvedAgentSettingsTests:
    @pytest.mark.parametrize(
        "overrides",
        [{"temperature": -0.1}, {"temperature": 2.1}, {"max_tokens": 0}],
    )
    def test_out_of_range_values_are_rejected_and_instance_is_frozen(self, overrides: dict[str, float | int]) -> None:
        valid = {
            "provider": "copilot",
            "model": None,
            "endpoint": None,
            "temperature": 0.2,
            "max_tokens": 4096,
            "tools": default_tool_policy(),
        }

        with pytest.raises(ValidationError):
            ResolvedAgentSettings.model_validate({**valid, **overrides})

        settings = ResolvedAgentSettings.model_validate(valid)
        with pytest.raises(ValidationError):
            settings.provider = "copilot"


class RegisteredProviderRedactionTests:
    @pytest.mark.parametrize(
        "token",
        [pytest.param(token, id=token) for token, spec in PROVIDERS.items() if spec.credential_envs],
    )
    def test_registered_credentialed_provider_masks_reflected_credential_on_invoke(
        self, token: str, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spec = PROVIDERS[token]
        values = [f"cr{index}x" for index, _ in enumerate(spec.credential_envs)]
        for name, value in zip(spec.credential_envs, values, strict=True):
            monkeypatch.setenv(name, value)
        adapter = spec.build()
        request = AgentRequestBuilder().with_worktree_path(git_repo).build()
        reflected = " ".join(values)
        outcomes = iter(
            [
                CliMutationOutcome(status="error", error_detail=reflected),
                CliMutationOutcome(status="finished", result_text=reflected),
            ]
        )

        def _reflecting_run(_adapter: CliDirectMutationAdapter, _request: CliMutationRunRequest) -> CliMutationOutcome:
            return next(outcomes)

        monkeypatch.setattr(type(adapter), "_default_run", _reflecting_run)
        failed = adapter.invoke(request)
        finished = adapter.invoke(request)

        reflecting_text = "\n".join([*failed.errors, finished.raw_text or ""])
        assert reflecting_text.count("[REDACTED:") >= len(values)
        assert not any(value in reflecting_text for value in values)

    def test_copilot_masks_reflected_credential_from_process_stderr_and_stream(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spec = PROVIDERS["copilot"]
        values = [f"cr{index}x" for index, _ in enumerate(spec.credential_envs)]
        for name, value in zip(spec.credential_envs, values, strict=True):
            monkeypatch.setenv(name, value)
        adapter = spec.build()
        runner = FakeAgentRunner()
        monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)
        request = AgentRequestBuilder().with_worktree_path(git_repo).build()
        reflected = " ".join(values)

        runner.returning(returncode=1, stderr=reflected.encode())
        failed = adapter.invoke(request)
        stream = json.dumps({"type": "assistant.message", "data": {"content": reflected}})
        runner.returning(stdout=f'{stream}\n{{"type":"result","data":{{"exitCode":0}}}}\n'.encode())
        finished = adapter.invoke(request)

        reflecting_text = "\n".join([*failed.errors, finished.raw_text or ""])
        assert reflecting_text.count("[REDACTED:") >= len(values)
        assert not any(value in reflecting_text for value in values)
