"""Shared secret masking for agent responses, step captures, session artifacts, and history reads."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

SECRET_ENV_SUFFIXES: Final[tuple[str, ...]] = ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_AUTH")
MIN_SECRET_LENGTH: Final[int] = 6
ENV_FILE_NAME: Final[str] = ".env"
REDACTED_PLACEHOLDER: Final[str] = "[REDACTED]"
_PLACEHOLDER_PATTERN: Final[str] = r"\[REDACTED(?::[^\]]*)?\]"

CREDENTIAL_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
)


def _is_secret_name(name: str) -> bool:
    """Return whether the upper-cased name ends with a SECRET_ENV_SUFFIXES entry."""
    return name.upper().endswith(SECRET_ENV_SUFFIXES)


def suffix_secrets(env: Mapping[str, str]) -> list[tuple[str, str]]:
    """Return (name, value) for every env entry whose upper-cased name ends with a SECRET_ENV_SUFFIXES entry and whose stripped value has at least MIN_SECRET_LENGTH characters."""
    secrets: list[tuple[str, str]] = []
    for name, value in env.items():
        stripped = value.strip()
        if _is_secret_name(name) and len(stripped) >= MIN_SECRET_LENGTH:
            secrets.append((name, stripped))

    return secrets


def credential_secrets(env: Mapping[str, str], names: Sequence[str]) -> list[tuple[str, str]]:
    """Return (name, stripped value) for every listed name with a nonblank value, with no length floor."""
    secrets: list[tuple[str, str]] = []
    for name in names:
        stripped = env.get(name, "").strip()
        if stripped:
            secrets.append((name, stripped))

    return secrets


def load_env_file_secrets(directory: Path, credential_names: Sequence[str] = ()) -> list[tuple[str, str]]:
    """Return (key, value) pairs from ``directory``/.env, or [] when the file is absent or unreadable.

    A pair is kept when its key is listed in ``credential_names`` and its value is nonblank (no length floor), or when its key ends with a SECRET_ENV_SUFFIXES entry and its value has at least MIN_SECRET_LENGTH characters.
    """
    try:
        content = (directory / ENV_FILE_NAME).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    secrets: list[tuple[str, str]] = []
    for line in content.splitlines():
        parsed = _parse_env_line(line)
        if parsed is not None and _is_env_file_secret(*parsed, credential_names):
            secrets.append(parsed)

    return secrets


def _is_env_file_secret(key: str, value: str, credential_names: Sequence[str]) -> bool:
    """Return whether a .env entry is a listed credential or a suffix-named value over the length floor."""
    if key in credential_names:
        return bool(value)

    return _is_secret_name(key) and len(value) >= MIN_SECRET_LENGTH


def _parse_env_line(line: str) -> tuple[str, str] | None:
    """Parse one .env line into (key, unquoted value), or None for blank, comment, or malformed lines."""
    text = line.strip()
    if not text or text.startswith("#"):
        return None

    text = text.removeprefix("export ")
    key, separator, raw_value = text.partition("=")
    key = key.strip()
    if not separator or not key:
        return None

    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return key, value[1:-1]

    return key, value.split(" #", 1)[0].strip()


class SecretRedactor:
    """Masks literal secret values and known credential formats in text.

    Secrets live in memory only; the instance is built per invocation and never serialized or logged.
    """

    def __init__(self, secrets: Sequence[tuple[str, str]]) -> None:
        """Build a redactor from (label, value) pairs; blank values are dropped and the first label wins per value."""
        labels: dict[str, str] = {}
        for label, value in secrets:
            if value.strip():
                labels.setdefault(value, label)

        self._labels = labels
        values = sorted(labels, key=len, reverse=True)
        self._value_pattern = re.compile("|".join([_PLACEHOLDER_PATTERN, *(re.escape(value) for value in values)]))

    @classmethod
    def from_environment(
        cls,
        *,
        env: Mapping[str, str] | None = None,
        credential_envs: Sequence[str] = (),
        env_file_dir: Path | None = None,
    ) -> SecretRedactor:
        """Build a redactor from ``env`` (default ``os.environ``), the named credential envs, and ``env_file_dir``/.env."""
        source = os.environ if env is None else env
        secrets = credential_secrets(source, credential_envs) + suffix_secrets(source)
        if env_file_dir is not None:
            secrets += load_env_file_secrets(env_file_dir, credential_envs)

        return cls(secrets)

    def redact_text(self, text: str) -> str:
        """Return ``text`` with secret values replaced by ``[REDACTED:<label>]`` and credential formats by ``[REDACTED]``."""
        text = self._value_pattern.sub(self._mask_match, text)

        for pattern in CREDENTIAL_PATTERNS:
            text = pattern.sub(REDACTED_PLACEHOLDER, text)

        return text

    def _mask_match(self, match: re.Match[str]) -> str:
        """Label a secret value; an existing placeholder matches first and is returned unchanged."""
        label = self._labels.get(match.group(0))

        return match.group(0) if label is None else f"[REDACTED:{label}]"

    def __repr__(self) -> str:
        """Return a representation that names the secret count and never the values."""
        return f"SecretRedactor(secrets={len(self._labels)})"
