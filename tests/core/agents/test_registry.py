"""Contract tests for the agent provider base class, descriptor registry, and resolved settings."""

from __future__ import annotations

import os
import subprocess
import urllib.request
from collections.abc import Iterator, MutableMapping
from pathlib import Path

import pytest
from pydantic import ValidationError

from dovo.core.agents import (
    AgentResponseStatus,
    BaseAgentProvider,
    ResolvedAgentSettings,
    default_tool_policy,
    get_agent_adapter,
)
from dovo.core.agents.base import ProviderSpec
from dovo.core.agents.cli_mutation import CliDirectMutationAdapter, CliMutationOutcome, CliMutationRunRequest
from dovo.core.agents.registry import PROVIDERS
from tests.harness import PROVIDER_FINISHED_STREAMS, AgentRequestBuilder, EditingAgentRunner, FakeAgentRunner


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

        adapters = [get_agent_adapter(spec.token) for spec in PROVIDERS.values()]

        assert all(isinstance(adapter, BaseAgentProvider) for adapter in adapters)

    def test_stream_factory_covers_exactly_the_registered_providers(self) -> None:
        assert set(PROVIDER_FINISHED_STREAMS) == set(PROVIDERS)
        assert all(key == spec.token for key, spec in PROVIDERS.items())

    @pytest.mark.parametrize("token", [pytest.param(token, id=token) for token in PROVIDERS])
    def test_get_agent_adapter_builds_exact_adapter_without_side_effects(
        self, token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in PROVIDERS[token].credential_envs:
            monkeypatch.delenv(name, raising=False)

        def _forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("adapter construction must not start a process or contact an endpoint")

        monkeypatch.setattr(subprocess, "Popen", _forbidden)
        monkeypatch.setattr(urllib.request, "urlopen", _forbidden)

        assert type(get_agent_adapter(token)) is PROVIDERS[token].build

    def test_unregistered_token_returns_none(self) -> None:
        assert get_agent_adapter("unregistered") is None


class ResolvedAgentSettingsTests:
    @pytest.mark.parametrize(
        "overrides",
        [{"temperature": -0.1}, {"temperature": 2.1}, {"max_tokens": 0}],
    )
    def test_out_of_range_values_are_rejected_and_instance_is_frozen(self, overrides: dict[str, float | int]) -> None:
        valid = {
            "provider": "unit-test",
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
            settings.provider = "unit-test"  # ty: ignore[invalid-assignment]  # intentional ill-typed test input: frozen-model write


def _reflect_credentials(spec: ProviderSpec, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Set each of spec's credential variables to a distinct value and return the values in order."""
    values = [f"cr{index}x" for index, _ in enumerate(spec.credential_envs)]
    for name, value in zip(spec.credential_envs, values, strict=True):
        monkeypatch.setenv(name, value)

    return values


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

    @pytest.mark.parametrize(
        "token",
        [pytest.param(token, id=token) for token, spec in PROVIDERS.items() if spec.credential_envs],
    )
    def test_registered_provider_masks_reflected_credential_at_the_process_boundary(
        self, token: str, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mask every credential value from stderr and the finished stream, one label per name."""
        spec = PROVIDERS[token]
        values = _reflect_credentials(spec, monkeypatch)
        adapter = spec.build()
        runner = FakeAgentRunner()
        monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)
        request = AgentRequestBuilder().with_worktree_path(git_repo).build()
        reflected = " ".join(values)

        runner.returning(returncode=1, stderr=reflected.encode())
        failed = adapter.invoke(request)
        runner.returning(stdout=PROVIDER_FINISHED_STREAMS[token](reflected))
        finished = adapter.invoke(request)

        text = "\n".join([*failed.errors, finished.raw_text or ""])
        assert not any(value in text for value in values)
        assert all(f"[REDACTED:{name}]" in text for name in spec.credential_envs)

    @pytest.mark.parametrize(
        "token",
        [pytest.param(token, id=token) for token, spec in PROVIDERS.items() if spec.credential_envs],
    )
    def test_registered_provider_masks_reflected_credential_in_a_proposed_patch_response(
        self, token: str, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return PROPOSED_PATCH with the credential masked in raw_text and an unmasked '+edited' diff."""
        spec = PROVIDERS[token]
        values = _reflect_credentials(spec, monkeypatch)
        runner = EditingAgentRunner({"edited.txt": "edited\n"}).returning(
            stdout=PROVIDER_FINISHED_STREAMS[token](" ".join(values))
        )
        monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)

        response = spec.build().invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        text = response.raw_text or ""
        assert response.status == AgentResponseStatus.PROPOSED_PATCH
        assert not any(value in text for value in values)
        assert all(f"[REDACTED:{name}]" in text for name in spec.credential_envs)
        assert response.unified_diff is not None
        assert "+edited" in response.unified_diff
