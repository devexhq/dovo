"""Tests for the explicit agent subprocess environment builder and its validation helpers."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from dovo.core.agents import AgentEnvMode, AgentInvocationContext
from dovo.core.agents.copilot import COPILOT_PROVIDER_SPEC
from dovo.core.agents.environment import (
    NETWORK_TLS_ENV_NAMES,
    WITHHELD_REPORT_LIMIT,
    build_agent_env,
    env_passthrough_flag_error,
    format_withheld_report,
    forwarded_env_secrets,
    validate_agent_env_request,
    withheld_env_names,
)
from tests.harness import AgentRequestBuilder

_OVERRIDE_MESSAGE = (
    "Agent environment override '{name}' conflicts with provider or Dovo controls (AGENT_ENV_OVERRIDE_INVALID). "
    "Fix: remove it from agent.env_passthrough or the agent step's env."
)


class _HostEnv(dict[str, str]):
    """Host environment double that ignores pytest's own PYTEST_CURRENT_TEST bookkeeping write."""

    def __setitem__(self, key: str, value: str) -> None:
        if key != "PYTEST_CURRENT_TEST":
            super().__setitem__(key, value)


@pytest.fixture
def host_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Replace os.environ with a minimal mutable mapping so every host name in a test is explicit."""
    host = _HostEnv({"PATH": "/usr/bin", "HOME": "/home/tester", "GH_TOKEN": "gh-token-value"})
    monkeypatch.setattr(os, "environ", host)

    return host


@pytest.fixture
def builder(tmp_path: Path) -> AgentRequestBuilder:
    """Return a request builder bound to a throwaway worktree path."""
    return AgentRequestBuilder().with_worktree_path(tmp_path)


@pytest.fixture
def invocation(tmp_path: Path) -> AgentInvocationContext:
    """Return an authored invocation whose scratch and control directories are siblings."""
    root = tmp_path / "invocation"

    return AgentInvocationContext(invocation_id="a" * 32, scratch_path=root / "scratch", control_path=root / "control")


class BuildAgentEnvTests:
    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("AWS_SECRET_ACCESS_KEY", id="aws"),
            pytest.param("SSH_AUTH_SOCK", id="ssh"),
            pytest.param("ANTHROPIC_API_KEY", id="other-provider"),
            pytest.param("DOVO_TEST_UNRELATED_SECRET", id="dovo-prefix"),
            pytest.param("DOVO_STEP_ID", id="ambient-dovo-metadata-name"),
        ],
    )
    def test_allowlist_mode_omits_unrelated_host_name(
        self, name: str, host_env: dict[str, str], builder: AgentRequestBuilder
    ) -> None:
        """[tier-1/unit] build_agent_env: allowlist mode with the host name set returns an env lacking it and containing PATH."""
        host_env[name] = "host-value"

        env = build_agent_env(COPILOT_PROVIDER_SPEC, builder.build())

        assert name not in env
        assert env["PATH"] == "/usr/bin"

    def test_inherit_mode_keeps_host_names_and_metadata_overrides_ambient(
        self, host_env: dict[str, str], builder: AgentRequestBuilder
    ) -> None:
        """[tier-1/unit] build_agent_env: inherit mode keeps AWS_SECRET_ACCESS_KEY and metadata_env DOVO_STEP_ID overrides an ambient DOVO_STEP_ID."""
        host_env.update({"AWS_SECRET_ACCESS_KEY": "aws", "DOVO_STEP_ID": "ambient"})
        request = builder.with_env_mode("inherit").with_metadata_env({"DOVO_STEP_ID": "generated"}).build()

        env = build_agent_env(COPILOT_PROVIDER_SPEC, request)

        assert env["AWS_SECRET_ACCESS_KEY"] == "aws"
        assert env["DOVO_STEP_ID"] == "generated"

    @pytest.mark.parametrize(
        ("platform", "expected_present"),
        [pytest.param("win32", True, id="windows"), pytest.param("linux", False, id="posix")],
    )
    def test_windows_base_names_forwarded_only_on_windows(
        self,
        platform: str,
        expected_present: bool,
        host_env: dict[str, str],
        builder: AgentRequestBuilder,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """[tier-1/unit] build_agent_env: SYSTEMROOT, WINDIR, COMSPEC, PATHEXT, USERPROFILE are in env exactly when sys.platform starts with 'win'."""
        names = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "USERPROFILE")
        host_env.update(dict.fromkeys(names, "win-value"))
        monkeypatch.setattr(sys, "platform", platform)

        env = build_agent_env(COPILOT_PROVIDER_SPEC, builder.build())

        assert [name in env for name in names] == [expected_present] * len(names)

    @pytest.mark.parametrize("name", [pytest.param(name, id=name) for name in NETWORK_TLS_ENV_NAMES])
    def test_network_tls_tier_name_is_forwarded_in_allowlist_mode(
        self, name: str, host_env: dict[str, str], builder: AgentRequestBuilder
    ) -> None:
        """[tier-1/unit] build_agent_env: each FR-6 name set on the host appears in env with its value."""
        host_env[name] = "network-value"

        env = build_agent_env(COPILOT_PROVIDER_SPEC, builder.build())

        assert env[name] == "network-value"

    @pytest.mark.parametrize("mode", [pytest.param("allowlist", id="allowlist"), pytest.param("inherit", id="inherit")])
    def test_authored_invocation_points_all_four_scratch_keys_at_scratch_path(
        self,
        mode: AgentEnvMode,
        host_env: dict[str, str],
        builder: AgentRequestBuilder,
        invocation: AgentInvocationContext,
    ) -> None:
        """[tier-1/unit] build_agent_env: DOVO_AGENT_SCRATCH, TMPDIR, TMP, TEMP all equal str(invocation.scratch_path) in both modes and no value equals str(control_path) or its parent."""
        host_env.update({"TMPDIR": "/ambient/tmp", "TMP": "/ambient/tmp", "TEMP": "/ambient/tmp"})
        request = builder.with_invocation(invocation).with_env_mode(mode)

        env = build_agent_env(COPILOT_PROVIDER_SPEC, request.build())

        scratch = str(invocation.scratch_path)
        assert [env[name] for name in ("DOVO_AGENT_SCRATCH", "TMPDIR", "TMP", "TEMP")] == [scratch] * 4
        assert str(invocation.control_path) not in env.values()
        assert str(invocation.control_path.parent) not in env.values()

    def test_request_without_invocation_keeps_ambient_tmpdir_and_no_scratch_key(
        self, host_env: dict[str, str], builder: AgentRequestBuilder
    ) -> None:
        """[tier-1/unit] build_agent_env: with invocation None, TMPDIR is the ambient value and DOVO_AGENT_SCRATCH is absent."""
        host_env["TMPDIR"] = "/ambient/tmp"

        env = build_agent_env(COPILOT_PROVIDER_SPEC, builder.build())

        assert env["TMPDIR"] == "/ambient/tmp"
        assert "DOVO_AGENT_SCRATCH" not in env

    def test_literal_passthrough_forwards_present_and_omits_absent_names(
        self, host_env: dict[str, str], builder: AgentRequestBuilder
    ) -> None:
        """[tier-1/unit] build_agent_env: ['DOCKER_CONFIG','MISSING_NAME'] forwards DOCKER_CONFIG and has no MISSING_NAME key."""
        host_env["DOCKER_CONFIG"] = "/docker"

        env = build_agent_env(
            COPILOT_PROVIDER_SPEC, builder.with_env_passthrough(["DOCKER_CONFIG", "MISSING_NAME"]).build()
        )

        assert env["DOCKER_CONFIG"] == "/docker"
        assert "MISSING_NAME" not in env

    def test_prefix_passthrough_forwards_matches_and_skips_reserved_names(
        self, host_env: dict[str, str], builder: AgentRequestBuilder
    ) -> None:
        """[tier-1/unit] build_agent_env: ['DOCKER_*','COPILOT_*'] forwards DOCKER_HOST and DOCKER_CONTEXT and omits COPILOT_ALLOW_ALL without raising."""
        host_env.update({"DOCKER_HOST": "unix:///d.sock", "DOCKER_CONTEXT": "ctx", "COPILOT_ALLOW_ALL": "1"})

        env = build_agent_env(COPILOT_PROVIDER_SPEC, builder.with_env_passthrough(["DOCKER_*", "COPILOT_*"]).build())

        assert (env["DOCKER_HOST"], env["DOCKER_CONTEXT"]) == ("unix:///d.sock", "ctx")
        assert "COPILOT_ALLOW_ALL" not in env

    def test_step_env_overrides_base_and_passthrough_and_adds_outside_allowlist_names(
        self, host_env: dict[str, str], builder: AgentRequestBuilder
    ) -> None:
        """[tier-1/unit] build_agent_env: step env PATH='/custom', passthrough value overridden, MY_VAR forwarded, all taking the step value."""
        host_env["DOCKER_CONFIG"] = "/host-docker"
        request = (
            builder.with_env_passthrough(["DOCKER_CONFIG"])
            .with_env({"MY_VAR": "x", "PATH": "/custom", "DOCKER_CONFIG": "/step-docker"})
            .build()
        )

        env = build_agent_env(COPILOT_PROVIDER_SPEC, request)

        assert (env["MY_VAR"], env["PATH"], env["DOCKER_CONFIG"]) == ("x", "/custom", "/step-docker")

    @pytest.mark.parametrize("mode", [pytest.param("allowlist", id="allowlist"), pytest.param("inherit", id="inherit")])
    def test_only_active_provider_credential_is_forwarded_under_winning_name(
        self, mode: AgentEnvMode, host_env: dict[str, str], builder: AgentRequestBuilder
    ) -> None:
        """[tier-1/unit] build_agent_env: GH_TOKEN=a, GITHUB_TOKEN=b, ANTHROPIC_API_KEY=z yields GH_TOKEN == 'a' and neither GITHUB_TOKEN nor ANTHROPIC_API_KEY, in both modes."""
        host_env.update({"GH_TOKEN": "a", "GITHUB_TOKEN": "b", "ANTHROPIC_API_KEY": "z"})
        request = builder.with_env_mode(mode).build()

        env = build_agent_env(COPILOT_PROVIDER_SPEC, request)

        assert env["GH_TOKEN"] == "a"
        assert "GITHUB_TOKEN" not in env
        assert ("ANTHROPIC_API_KEY" in env) == (mode == "inherit")

    @pytest.mark.parametrize("mode", [pytest.param("allowlist", id="allowlist"), pytest.param("inherit", id="inherit")])
    def test_adapter_controls_are_stripped_in_both_modes(
        self, mode: AgentEnvMode, host_env: dict[str, str], builder: AgentRequestBuilder
    ) -> None:
        """[tier-1/unit] build_agent_env: ambient COPILOT_ALLOW_ALL, COPILOT_GITHUB_TOKEN, COPILOT_MODEL are absent in allowlist and inherit modes."""
        controls = ("COPILOT_ALLOW_ALL", "COPILOT_GITHUB_TOKEN", "COPILOT_MODEL")
        host_env.update(dict.fromkeys(controls, "ambient"))

        env = build_agent_env(COPILOT_PROVIDER_SPEC, builder.with_env_mode(mode).build())

        assert [name in env for name in controls] == [False] * 3

    def test_withheld_names_are_sorted_and_reported_in_allowlist_mode(
        self, host_env: dict[str, str], builder: AgentRequestBuilder
    ) -> None:
        """[tier-1/unit] build_agent_env: host-only names ZED, FOO, AWS_KEY_X give env['DOVO_ENV_WITHHELD'] == 'AWS_KEY_X,FOO,ZED'."""
        host_env.update({"ZED": "1", "FOO": "2", "AWS_KEY_X": "3"})

        env = build_agent_env(COPILOT_PROVIDER_SPEC, builder.build())

        assert env["DOVO_ENV_WITHHELD"] == "AWS_KEY_X,FOO,ZED"

    @pytest.mark.parametrize(
        "mode", [pytest.param("inherit", id="inherit"), pytest.param("allowlist", id="nothing-withheld")]
    )
    def test_no_withheld_report_when_inherit_or_nothing_withheld(
        self, mode: AgentEnvMode, host_env: dict[str, str], builder: AgentRequestBuilder
    ) -> None:
        """[tier-1/unit] build_agent_env: DOVO_ENV_WITHHELD is absent from env, including when the host exports its own in inherit mode."""
        if mode == "inherit":
            host_env["DOVO_ENV_WITHHELD"] = "ambient"

        env = build_agent_env(COPILOT_PROVIDER_SPEC, builder.with_env_mode(mode).build())

        assert "DOVO_ENV_WITHHELD" not in env

    def test_changed_credential_passthrough_and_mode_are_seen_on_the_next_call(
        self, host_env: dict[str, str], tmp_path: Path
    ) -> None:
        """[tier-1/unit] build_agent_env: two calls with GH_TOKEN, a passthrough value, and env_mode changed between them return the new values from the second call (NFR-2)."""
        host_env.update({"GH_TOKEN": "first", "DOCKER_CONFIG": "/one", "UNRELATED": "u"})
        first = build_agent_env(
            COPILOT_PROVIDER_SPEC,
            AgentRequestBuilder().with_worktree_path(tmp_path).with_env_passthrough(["DOCKER_CONFIG"]).build(),
        )

        host_env.update({"GH_TOKEN": "second", "DOCKER_CONFIG": "/two"})
        second = build_agent_env(
            COPILOT_PROVIDER_SPEC,
            AgentRequestBuilder()
            .with_worktree_path(tmp_path)
            .with_env_passthrough(["DOCKER_CONFIG"])
            .with_env_mode("inherit")
            .build(),
        )

        assert (first["GH_TOKEN"], first["DOCKER_CONFIG"], "UNRELATED" in first) == ("first", "/one", False)
        assert (second["GH_TOKEN"], second["DOCKER_CONFIG"], "UNRELATED" in second) == ("second", "/two", True)


class WithheldEnvNamesTests:
    def test_allowlist_mode_returns_sorted_host_names_missing_from_env(
        self, host_env: dict[str, str], builder: AgentRequestBuilder
    ) -> None:
        """[tier-1/unit] withheld_env_names: allowlist mode returns the sorted host names absent from the built env."""
        host_env.update({"ZED": "1", "ALPHA": "2"})

        assert withheld_env_names(builder.build(), {"PATH": "/usr/bin"}) == ["ALPHA", "GH_TOKEN", "HOME", "ZED"]

    def test_inherit_mode_returns_no_names(self, host_env: dict[str, str], builder: AgentRequestBuilder) -> None:
        """[tier-1/unit] withheld_env_names: inherit mode returns [] even when the env is empty."""
        assert withheld_env_names(builder.with_env_mode("inherit").build(), {}) == []


class FormatWithheldReportTests:
    def test_report_over_limit_is_at_most_4096_characters_and_ends_with_dropped_count(self) -> None:
        """[tier-1/unit] format_withheld_report: 1000 names of 12 characters give len(result) <= 4096 and a final ',+N more' where N equals the dropped count."""
        names = [f"NAME_{index:07d}" for index in range(1000)]

        report = format_withheld_report(names)

        kept, _, tail = report.rpartition(",")
        assert len(report) <= WITHHELD_REPORT_LIMIT
        assert tail == f"+{1000 - len(kept.split(','))} more"

    def test_report_within_limit_is_the_plain_comma_join(self) -> None:
        """[tier-1/unit] format_withheld_report: ['A','B'] returns 'A,B'."""
        assert format_withheld_report(["A", "B"]) == "A,B"


class ValidateAgentEnvRequestTests:
    @pytest.mark.parametrize(
        "source", [pytest.param("passthrough", id="passthrough"), pytest.param("step-env", id="step-env")]
    )
    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("COPILOT_MODEL", id="control"),
            pytest.param("DOVO_STEP_ID", id="metadata"),
            pytest.param("DOVO_ENV_WITHHELD", id="withheld"),
            pytest.param("TMPDIR", id="scratch-temp"),
            pytest.param("DOVO_AGENT_SCRATCH", id="scratch-name"),
        ],
    )
    def test_reserved_name_returns_the_fixed_override_message(
        self, source: str, name: str, builder: AgentRequestBuilder, invocation: AgentInvocationContext
    ) -> None:
        """[tier-1/unit] validate_agent_env_request: an authored invocation naming a reserved name returns "Agent environment override '<name>' conflicts with provider or Dovo controls (AGENT_ENV_OVERRIDE_INVALID). Fix: remove it from agent.env_passthrough or the agent step's env."."""
        request = builder.with_invocation(invocation).with_metadata_env({"DOVO_STEP_ID": "s"})
        request = request.with_env_passthrough([name]) if source == "passthrough" else request.with_env({name: "x"})

        assert validate_agent_env_request(COPILOT_PROVIDER_SPEC, request.build()) == _OVERRIDE_MESSAGE.format(name=name)

    def test_step_env_credential_name_is_rejected_but_passthrough_credential_name_is_allowed(
        self, builder: AgentRequestBuilder, tmp_path: Path
    ) -> None:
        """[tier-1/unit] validate_agent_env_request: step env {'GH_TOKEN': 'x'} returns the message; env_passthrough ['GH_TOKEN'] returns None."""
        rejected = builder.with_env({"GH_TOKEN": "x"}).build()
        allowed = AgentRequestBuilder().with_worktree_path(tmp_path).with_env_passthrough(["GH_TOKEN"]).build()

        assert validate_agent_env_request(COPILOT_PROVIDER_SPEC, rejected) == _OVERRIDE_MESSAGE.format(name="GH_TOKEN")
        assert validate_agent_env_request(COPILOT_PROVIDER_SPEC, allowed) is None

    def test_scratch_names_are_not_reserved_without_an_invocation(self, builder: AgentRequestBuilder) -> None:
        """[tier-1/unit] validate_agent_env_request: invocation None with step env TMPDIR returns None."""
        assert validate_agent_env_request(COPILOT_PROVIDER_SPEC, builder.with_env({"TMPDIR": "/t"}).build()) is None

    def test_prefix_pattern_matching_a_reserved_name_is_not_an_error(self, builder: AgentRequestBuilder) -> None:
        """[tier-1/unit] validate_agent_env_request: env_passthrough ['COPILOT_*'] returns None."""
        assert (
            validate_agent_env_request(COPILOT_PROVIDER_SPEC, builder.with_env_passthrough(["COPILOT_*"]).build())
            is None
        )


class EnvPassthroughFlagErrorTests:
    @pytest.mark.parametrize(
        "entry", [pytest.param("DOCKER_HOST", id="literal"), pytest.param("DOCKER_*", id="prefix")]
    )
    def test_valid_entry_returns_none(self, entry: str) -> None:
        """[tier-1/unit] env_passthrough_flag_error: a literal or single-trailing-star entry returns None."""
        assert env_passthrough_flag_error([entry]) is None

    @pytest.mark.parametrize(
        "entry",
        [
            pytest.param("*", id="bare-star"),
            pytest.param("A*B", id="inner-star"),
            pytest.param("A=B", id="equals"),
            pytest.param("", id="empty"),
            pytest.param("A\x00B", id="nul"),
        ],
    )
    def test_invalid_entry_returns_the_fixed_message(self, entry: str) -> None:
        """[tier-1/unit] env_passthrough_flag_error: returns "Invalid --env-passthrough value '<entry>': use a variable name or a prefix ending in a single '*'. Fix: for example --env-passthrough DOCKER_HOST or --env-passthrough 'DOCKER_*'."."""
        assert env_passthrough_flag_error(["OK", entry]) == (
            f"Invalid --env-passthrough value '{entry}': use a variable name or a prefix ending in a single '*'. "
            "Fix: for example --env-passthrough DOCKER_HOST or --env-passthrough 'DOCKER_*'."
        )


class ForwardedEnvSecretsTests:
    def test_suffix_named_step_env_value_and_proxy_password_are_returned_with_labels(
        self, host_env: dict[str, str], builder: AgentRequestBuilder
    ) -> None:
        """[tier-1/unit] forwarded_env_secrets: step env MY_API_KEY='abcdef123' and HTTPS_PROXY='http://u:p4ss@proxy:8080' return [('MY_API_KEY','abcdef123'), ('HTTPS_PROXY','p4ss')]."""
        host_env["HTTPS_PROXY"] = "http://u:p4ss@proxy:8080"

        secrets = forwarded_env_secrets(builder.with_env({"MY_API_KEY": "abcdef123"}).build())

        assert secrets == [("MY_API_KEY", "abcdef123"), ("HTTPS_PROXY", "p4ss")]

    @pytest.mark.parametrize(
        "proxy",
        [pytest.param("http://proxy:8080", id="no-userinfo"), pytest.param("http://[::1", id="unparsable")],
    )
    def test_proxy_url_without_a_password_registers_no_secret(
        self, proxy: str, host_env: dict[str, str], builder: AgentRequestBuilder
    ) -> None:
        """[tier-1/unit] forwarded_env_secrets: HTTPS_PROXY='http://proxy:8080' and an unparsable proxy value return []."""
        host_env["HTTPS_PROXY"] = proxy

        assert forwarded_env_secrets(builder.build()) == []
