"""Explicit subprocess environment for Dovo-owned agent providers: allowlist by default, explicit inherit mode."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping, Sequence
from typing import Final, Protocol
from urllib.parse import urlsplit

from dovo.common.redact import suffix_secrets
from dovo.core.agents.credentials import resolve_credential_entry
from dovo.core.agents.models import ENV_PASSTHROUGH_PATTERN, AgentRequest

BASE_ENV_NAMES: Final[tuple[str, ...]] = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR", "TERM")
WINDOWS_ENV_NAMES: Final[tuple[str, ...]] = (
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "USERPROFILE",
)
NETWORK_TLS_ENV_NAMES: Final[tuple[str, ...]] = (
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "DOCKER_HOST",
)
PROXY_URL_ENV_NAMES: Final[tuple[str, ...]] = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
AGENT_SCRATCH_ENV_NAME: Final[str] = "DOVO_AGENT_SCRATCH"
SCRATCH_TEMP_ENV_NAMES: Final[tuple[str, ...]] = ("TMPDIR", "TMP", "TEMP")
WITHHELD_ENV_NAME: Final[str] = "DOVO_ENV_WITHHELD"
WITHHELD_REPORT_LIMIT: Final[int] = 4096
ENV_OVERRIDE_INVALID_CODE: Final[str] = "AGENT_ENV_OVERRIDE_INVALID"
ENV_FILTERED_PROMPT_LINE: Final[str] = (
    "Environment is filtered. Variables withheld from this process are listed in $DOVO_ENV_WITHHELD. "
    "If a needed variable is missing, tell the user to add it to agent.env_passthrough or pass --env-passthrough."
)


class _EnvDescriptor(Protocol):
    """Structural view of ``ProviderSpec`` so this module need not import ``base.py``, which imports it."""

    @property
    def credential_envs(self) -> tuple[str, ...]:
        """Return the credential environment variable names, in precedence order."""
        ...

    @property
    def control_envs(self) -> tuple[str, ...]:
        """Return the adapter-owned control variable names."""
        ...


def build_agent_env(spec: _EnvDescriptor, request: AgentRequest) -> dict[str, str]:
    """Return a fresh subprocess environment for one invocation, read from the live host environment."""
    host: Mapping[str, str] = os.environ
    reserved = _reserved_names(spec, request)
    credential, stripped_credentials = _credential_env(spec)

    env = _base_env(host, request)
    for name in (*spec.control_envs, *stripped_credentials, WITHHELD_ENV_NAME):
        env.pop(name, None)

    env.update(request.metadata_env)
    env.update(_scratch_env(request))
    env.update(_passthrough_env(host, request, reserved | frozenset(spec.credential_envs)))
    env.update(credential)
    env.update(
        {
            name: value
            for name, value in request.env.items()
            if name not in reserved and name not in spec.credential_envs
        }
    )

    withheld = withheld_env_names(request, env)
    if withheld:
        env[WITHHELD_ENV_NAME] = format_withheld_report(withheld)

    return env


def validate_agent_env_request(spec: _EnvDescriptor, request: AgentRequest) -> str | None:
    """Return the fixed AGENT_ENV_OVERRIDE_INVALID message for the first reserved name in literal passthrough or step env, else None."""
    reserved = _reserved_names(spec, request)
    literal_passthrough = {entry for entry in request.env_passthrough if not entry.endswith("*")}
    conflicts = (literal_passthrough & reserved) | (set(request.env) & (reserved | frozenset(spec.credential_envs)))
    if not conflicts:
        return None

    name = min(conflicts)

    return (
        f"Agent environment override '{name}' conflicts with provider or Dovo controls "
        f"({ENV_OVERRIDE_INVALID_CODE}). Fix: remove it from agent.env_passthrough or the agent step's env."
    )


def withheld_env_names(request: AgentRequest, env: Mapping[str, str]) -> list[str]:
    """Return the sorted host names absent from ``env`` in allowlist mode, or [] in inherit mode."""
    if request.env_mode == "inherit":
        return []

    return sorted((set(os.environ) - set(env)) - {WITHHELD_ENV_NAME})


def format_withheld_report(names: Sequence[str]) -> str:
    """Join names with commas, dropping trailing names to fit WITHHELD_REPORT_LIMIT and ending with ',+N more'."""
    joined = ",".join(names)
    if len(joined) <= WITHHELD_REPORT_LIMIT:
        return joined

    kept = 0
    names_length = 0
    for idx, name in enumerate(names):
        names_length += len(name) + (1 if idx else 0)
        suffix = f"+{len(names) - idx - 1} more"
        if names_length + 1 + len(suffix) > WITHHELD_REPORT_LIMIT:
            break

        kept = idx + 1

    return ",".join([*names[:kept], f"+{len(names) - kept} more"])


def forwarded_env_secrets(request: AgentRequest) -> list[tuple[str, str]]:
    """Return redactor (label, value) pairs for sensitive explicit step env values and forwarded proxy-URL passwords."""
    secrets = suffix_secrets(request.env)
    forwarded = {**os.environ, **request.env}
    for name in PROXY_URL_ENV_NAMES:
        password = _url_password(forwarded.get(name, ""))
        if password:
            secrets.append((name, password))

    return secrets


def env_passthrough_flag_error(entries: Sequence[str]) -> str | None:
    """Return the fixed invalid --env-passthrough message for the first invalid entry, else None."""
    for entry in entries:
        if re.fullmatch(ENV_PASSTHROUGH_PATTERN, entry) is None:
            return (
                f"Invalid --env-passthrough value '{entry}': use a variable name or a prefix ending in a single '*'. "
                "Fix: for example --env-passthrough DOCKER_HOST or --env-passthrough 'DOCKER_*'."
            )

    return None


def _url_password(value: str) -> str | None:
    """Return the password component of a URL value, or None when absent or unparsable."""
    try:
        return urlsplit(value).password
    except ValueError:
        return None


def _reserved_names(spec: _EnvDescriptor, request: AgentRequest) -> frozenset[str]:
    """Return control, generated-metadata, withheld-report, and (for authored invocations) scratch names."""
    reserved = {*spec.control_envs, *request.metadata_env, WITHHELD_ENV_NAME}
    if request.invocation is not None:
        reserved.update((AGENT_SCRATCH_ENV_NAME, *SCRATCH_TEMP_ENV_NAMES))

    return frozenset(reserved)


def _base_env(host: Mapping[str, str], request: AgentRequest) -> dict[str, str]:
    """Return the allowlisted ambient base, or a full host copy in inherit mode."""
    if request.env_mode == "inherit":
        return dict(host)

    platform_names = WINDOWS_ENV_NAMES if sys.platform.startswith("win") else ()
    names = (*BASE_ENV_NAMES, *platform_names, *NETWORK_TLS_ENV_NAMES)

    return {name: host[name] for name in names if name in host}


def _scratch_env(request: AgentRequest) -> dict[str, str]:
    """Return DOVO_AGENT_SCRATCH and TMPDIR/TMP/TEMP from invocation.scratch_path, or {} without an invocation."""
    if request.invocation is None:
        return {}

    scratch = str(request.invocation.scratch_path)

    return dict.fromkeys((AGENT_SCRATCH_ENV_NAME, *SCRATCH_TEMP_ENV_NAMES), scratch)


def _passthrough_env(host: Mapping[str, str], request: AgentRequest, skipped: frozenset[str]) -> dict[str, str]:
    """Return present literal and prefix-matched host values, excluding skipped names."""
    forwarded: dict[str, str] = {}
    for entry in request.env_passthrough:
        for name in _matching_host_names(host, entry):
            if name not in skipped:
                forwarded[name] = host[name]

    return forwarded


def _matching_host_names(host: Mapping[str, str], entry: str) -> list[str]:
    """Return the host names a literal or trailing-star passthrough entry selects."""
    if entry.endswith("*"):
        prefix = entry.removesuffix("*")

        return [name for name in host if name.startswith(prefix)]

    return [entry] if entry in host else []


def _credential_env(spec: _EnvDescriptor) -> tuple[dict[str, str], frozenset[str]]:
    """Return the resolved credential under its winning name and the non-winning alternative names to strip."""
    entry = resolve_credential_entry(spec.credential_envs)
    winner = {} if entry is None else {entry[0]: entry[1]}

    return winner, frozenset(spec.credential_envs) - frozenset(winner)
