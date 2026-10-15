"""Tests for shared secret masking."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.redact import SecretRedactor, load_env_file_secrets


class SecretRedactorTests:
    @pytest.mark.parametrize("suffix", ["_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_AUTH"])
    def test_redact_text_masks_env_value_with_name_label_for_each_suffix(
        self, monkeypatch: pytest.MonkeyPatch, suffix: str
    ) -> None:
        monkeypatch.setenv(f"SVC{suffix}", "s3cr3t-value")

        redactor = SecretRedactor.from_environment()

        assert redactor.redact_text("x s3cr3t-value y") == f"x [REDACTED:SVC{suffix}] y"

    def test_redact_text_applies_six_character_floor_to_suffix_env_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MY_TOKEN", "abcde")
        monkeypatch.setenv("OTHER_TOKEN", "abcdef")

        redactor = SecretRedactor.from_environment()

        assert redactor.redact_text("abcde abcdef") == "abcde [REDACTED:OTHER_TOKEN]"

    def test_redact_text_ignores_env_names_without_a_secret_suffix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SERVICE_NAME", "long-value-here")

        redactor = SecretRedactor.from_environment()

        assert redactor.redact_text("long-value-here") == "long-value-here"

    def test_redact_text_masks_credential_envs_below_the_floor_and_trims_them(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mask " abc " as "abc" and "xy" for credential_envs below the floor, skipping a blank value."""
        monkeypatch.setenv("A_ID", " abc ")
        monkeypatch.setenv("B_ID", "xy")
        monkeypatch.setenv("C_ID", "   ")

        redactor = SecretRedactor.from_environment(credential_envs=("A_ID", "B_ID", "C_ID"))

        assert redactor.redact_text("abc xy   ") == "[REDACTED:A_ID] [REDACTED:B_ID]   "

    @pytest.mark.parametrize(
        "token",
        [
            pytest.param("ghp_" + "a" * 36, id="github-classic"),
            pytest.param("github_pat_" + "a" * 22, id="github-fine-grained"),
            pytest.param("sk-ant-" + "a" * 20, id="anthropic"),
            pytest.param("AKIA" + "A" * 16, id="aws-access-key-id"),
        ],
    )
    def test_redact_text_masks_credential_formats_without_a_label(self, token: str) -> None:
        redactor = SecretRedactor.from_environment(env={})

        assert redactor.redact_text(f"v={token}") == "v=[REDACTED]"

    def test_redact_text_is_stable_when_applied_twice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SVC_TOKEN", "s3cr3t-value")
        redactor = SecretRedactor.from_environment()
        text = "s3cr3t-value ghp_" + "a" * 36

        once = redactor.redact_text(text)

        assert once == "[REDACTED:SVC_TOKEN] [REDACTED]"
        assert redactor.redact_text(once) == once

    def test_redact_text_returns_text_without_secrets_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SVC_TOKEN", "s3cr3t-value")

        redactor = SecretRedactor.from_environment()

        assert redactor.redact_text("plain build output\n") == "plain build output\n"

    def test_redact_text_masks_the_longer_overlapping_secret_whole(self) -> None:
        redactor = SecretRedactor([("A_KEY", "abcdef"), ("B_KEY", "abcdefghi")])

        assert redactor.redact_text("abcdefghi") == "[REDACTED:B_KEY]"

    @pytest.mark.parametrize("value", ["REDACTED", "X_KEY", "SVC", "TOKEN", "]"])
    def test_redact_text_leaves_existing_placeholders_unchanged_for_colliding_values(self, value: str) -> None:
        redactor = SecretRedactor([("SVC_TOKEN", "s3cr3t-value"), ("X_KEY", value)])
        text = "[REDACTED] [REDACTED:SVC_TOKEN] [REDACTED:X_KEY]"

        assert redactor.redact_text(text) == text
        assert redactor.redact_text(f"{value} s3cr3t-value") == "[REDACTED:X_KEY] [REDACTED:SVC_TOKEN]"

    def test_repr_never_contains_secret_values(self) -> None:
        redactor = SecretRedactor([("SVC_TOKEN", "s3cr3t-value")])

        assert "s3cr3t-value" not in repr(redactor)

    def test_from_environment_reads_os_environ_at_call_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LATE_TOKEN", "s3cr3t-value")
        masking = SecretRedactor.from_environment()
        monkeypatch.delenv("LATE_TOKEN")
        unmasking = SecretRedactor.from_environment()

        assert masking.redact_text("s3cr3t-value") == "[REDACTED:LATE_TOKEN]"
        assert unmasking.redact_text("s3cr3t-value") == "s3cr3t-value"


class EnvFileSecretsTests:
    def test_load_env_file_secrets_parses_export_quotes_and_comments(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text(
            'export A_TOKEN="quoted-value"\n'
            "B_SECRET=plain-value # note\n"
            "# C_KEY=ignored-value\n"
            "D_KEY=abc\n"
            "APP_MODE=development\n",
            encoding="utf-8",
        )

        assert load_env_file_secrets(tmp_path) == [("A_TOKEN", "quoted-value"), ("B_SECRET", "plain-value")]

    def test_redact_text_masks_only_secret_looking_env_file_keys(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("DB_PASSWORD=hunter2-long\nAPP_MODE=development\n", encoding="utf-8")

        redactor = SecretRedactor.from_environment(env={}, env_file_dir=tmp_path)

        assert redactor.redact_text("hunter2-long development") == "[REDACTED:DB_PASSWORD] development"

    def test_load_env_file_secrets_returns_empty_for_missing_or_non_utf8_file(self, tmp_path: Path) -> None:
        binary_dir = tmp_path / "binary"
        binary_dir.mkdir()
        (binary_dir / ".env").write_bytes(b"\xff\xfe=")

        assert load_env_file_secrets(tmp_path) == []
        assert load_env_file_secrets(binary_dir) == []

    def test_load_env_file_secrets_keeps_listed_names_below_the_floor_without_a_secret_suffix(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".env").write_text("DB_PIN=1234\nOTHER_PIN=5678\nBLANK_PIN=\n", encoding="utf-8")

        assert load_env_file_secrets(tmp_path, ("DB_PIN", "BLANK_PIN")) == [("DB_PIN", "1234")]

    def test_redact_text_masks_listed_name_set_only_in_env_file(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("DB_PIN=1234\n", encoding="utf-8")

        listed = SecretRedactor.from_environment(env={}, credential_envs=("DB_PIN",), env_file_dir=tmp_path)
        unlisted = SecretRedactor.from_environment(env={}, env_file_dir=tmp_path)

        assert listed.redact_text("pin 1234") == "pin [REDACTED:DB_PIN]"
        assert unlisted.redact_text("pin 1234") == "pin 1234"
