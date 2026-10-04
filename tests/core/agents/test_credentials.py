"""Tests for shared agent credential lookup and canonical missing-credential diagnostics."""

from __future__ import annotations

import pytest

from dovo.core.agents.base import ProviderSpec
from dovo.core.agents.copilot import CopilotAgentAdapter
from dovo.core.agents.credentials import missing_credential_error, resolve_credential


def _spec(envs: tuple[str, ...], token: str = "x") -> ProviderSpec:
    return ProviderSpec(
        token=token,
        credential_envs=envs,
        requires_model=False,
        supports_tool_policy=False,
        supports_os_sandbox=False,
        build=CopilotAgentAdapter,
    )


class CredentialResolutionTests:
    @pytest.mark.parametrize(
        ("env", "expected"),
        [
            pytest.param({"A": "1", "B": "2"}, "1", id="first-wins"),
            pytest.param({"B": "2"}, "2", id="absent-first-skipped"),
            pytest.param({"A": "", "B": "2"}, "2", id="empty-first-skipped"),
            pytest.param({"A": "   ", "B": "2"}, "2", id="whitespace-first-skipped"),
            pytest.param({"A": " 1 \n"}, "1", id="surrounding-whitespace-stripped"),
            pytest.param({"A": " ", "B": ""}, None, id="all-blank-returns-none"),
            pytest.param({}, None, id="empty-mapping-returns-none"),
        ],
    )
    def test_process_environment_resolves_first_usable_value(
        self, monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: str | None
    ) -> None:
        """[tier-1/unit] resolve_credential: envs ("A","B") over the process environment return the first non-blank stripped value, else None."""
        for name in ("A", "B"):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)

        assert resolve_credential(("A", "B")) == expected

    def test_process_environment_changes_between_calls_are_observed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """[tier-1/unit] resolve_credential: with env omitted, A unset returns None, then A="a" set returns "a", then A="  " set returns None."""
        monkeypatch.delenv("A", raising=False)
        before = resolve_credential(("A",))

        monkeypatch.setenv("A", "a")
        during = resolve_credential(("A",))

        monkeypatch.setenv("A", "  ")
        after = resolve_credential(("A",))

        assert (before, during, after) == (None, "a", None)


class MissingCredentialErrorTests:
    @pytest.mark.parametrize(
        ("envs", "expected"),
        [
            pytest.param(
                ("GH_TOKEN", "GITHUB_TOKEN"),
                "missing GH_TOKEN or GITHUB_TOKEN. Fix: export GH_TOKEN=... or export GITHUB_TOKEN=...",
                id="two-alternatives",
            ),
            pytest.param(
                ("ANTHROPIC_API_KEY",),
                "missing ANTHROPIC_API_KEY. Fix: export ANTHROPIC_API_KEY=...",
                id="single",
            ),
            pytest.param(
                ("A", "B", "C"),
                "missing A or B or C. Fix: export A=... or export B=... or export C=...",
                id="three-alternatives-descriptor-order",
            ),
        ],
    )
    def test_descriptor_names_render_in_order_without_prefix(self, envs: tuple[str, ...], expected: str) -> None:
        """[tier-1/unit] missing_credential_error: a spec with the given credential_envs returns the exact unprefixed string."""
        assert missing_credential_error(_spec(envs)) == expected

    def test_empty_credential_envs_raises_value_error(self) -> None:
        """[tier-1/unit] missing_credential_error: credential_envs=() raises ValueError "provider 'x' declares no credential_envs"."""
        with pytest.raises(ValueError, match="provider 'x' declares no credential_envs"):
            missing_credential_error(_spec(()))
