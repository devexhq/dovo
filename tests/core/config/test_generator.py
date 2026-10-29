"""Contract tests for default config generation."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import Filesystem, WorkspacePaths
from dovo.common.models import Remediation
from dovo.common.schema_validation import CONFIG_VALIDATOR
from dovo.core.config.exceptions import ConfigError
from dovo.core.config.generator import build_default_config, generate_default_config
from dovo.core.config.models import ConfigGenerationSummary

_WRITE_ERROR = PermissionError(13, "Permission denied")
_INVALID_VERSION_CONFIG = {**build_default_config("demo"), "version": "one"}
_INVALID_JSON_REMEDIATIONS = [
    Remediation(message="Repair invalid JSON manually"),
    Remediation(message="Run 'dovo init --overwrite'", command="dovo init --overwrite"),
]


class BuildDefaultConfigTests:
    def test_build_default_config_contains_default_agent_and_empty_agents(self) -> None:
        """Return default_agent "copilot", agents {}, and no agent key."""
        payload = build_default_config("demo")

        assert (payload["default_agent"], payload["agents"], "agent" in payload) == ("copilot", {}, False)
        assert CONFIG_VALIDATOR.validate(payload).ok


class GenerateDefaultConfigTests:
    @pytest.mark.parametrize(
        ("existing", "project_name", "overwrite", "repair", "expected_flags", "expected_name"),
        [
            pytest.param(None, "demo", False, False, (True, False, False, False), "demo", id="create"),
            pytest.param(
                "original-name",
                "new-name",
                False,
                False,
                (False, True, False, False),
                "original-name",
                id="skip-existing",
            ),
            pytest.param(
                "original-name",
                "replaced-name",
                True,
                False,
                (False, False, True, False),
                "replaced-name",
                id="overwrite",
            ),
            pytest.param(
                "original-name", "new-name", False, True, (False, False, False, True), "original-name", id="repair"
            ),
        ],
    )
    def test_policy_writes_or_skips_config(
        self,
        workspace_paths: WorkspacePaths,
        existing: str | None,
        project_name: str,
        overwrite: bool,
        repair: bool,
        expected_flags: tuple[bool, bool, bool, bool],
        expected_name: str,
    ) -> None:
        """Create a missing config, skip an existing one, replace it with overwrite, or repair it, returning one flag."""
        config_file = workspace_paths.config_file
        if existing is not None:
            Filesystem.atomic_write_json(config_file, build_default_config(existing))
        before = config_file.read_bytes() if existing is not None else None

        summary = generate_default_config(config_file, project_name, overwrite=overwrite, repair=repair)

        assert isinstance(summary, ConfigGenerationSummary)
        assert (summary.created, summary.skipped_existing, summary.overwritten, summary.repaired) == expected_flags
        assert (summary.config_path, summary.inserted_keys, summary.warnings) == (config_file.resolve(), (), ())
        written = json.loads(config_file.read_text(encoding="utf-8"))
        expected = build_default_config(expected_name)
        expected["project"]["initialized_at"] = written["project"]["initialized_at"]
        assert written == expected
        if summary.skipped_existing:
            assert config_file.read_bytes() == before

    def test_overwrite_with_repair_warns_and_overwrites(self, workspace_paths: WorkspacePaths) -> None:
        """Return the repair-ignored warning and overwrite the existing config."""
        Filesystem.atomic_write_json(workspace_paths.config_file, build_default_config("original-name"))

        summary = generate_default_config(workspace_paths.config_file, "demo", overwrite=True, repair=True)

        assert (summary.warnings, summary.overwritten, summary.repaired) == (
            ("repair ignored when overwrite=True",),
            True,
            False,
        )


class GenerateDefaultConfigRepairTests:
    def test_repair_inserts_agent_keys_into_a_config_written_before_them(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.json"
        generate_default_config(config_path, "demo")
        legacy = json.loads(config_path.read_text(encoding="utf-8"))
        del legacy["default_agent"], legacy["agents"]
        config_path.write_text(json.dumps(legacy), encoding="utf-8")

        summary = generate_default_config(config_path, "demo", repair=True)

        repaired = json.loads(config_path.read_text(encoding="utf-8"))
        assert summary.inserted_keys == ("default_agent", "agents")
        assert (repaired["default_agent"], repaired["agents"]) == ("copilot", {})
        assert CONFIG_VALIDATOR.validate(repaired).ok


def _directory_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "config.json").mkdir()
    return tmp_path / "config.json"


def _missing_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return tmp_path / "absent" / "config.json"


def _unwritable_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(os, "access", lambda *_args, **_kwargs: False)
    return tmp_path / "config.json"


def _invalid_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "config.json").write_text("{bad json", encoding="utf-8")
    return tmp_path / "config.json"


def _list_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "config.json").write_text("[]", encoding="utf-8")
    return tmp_path / "config.json"


def _schema_invalid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "config.json").write_text(json.dumps(_INVALID_VERSION_CONFIG), encoding="utf-8")
    return tmp_path / "config.json"


def _write_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    def _raise(*_args: object, **_kwargs: object) -> None:
        raise _WRITE_ERROR

    monkeypatch.setattr(Filesystem, "atomic_write_json", staticmethod(_raise))
    return tmp_path / "config.json"


def _none(config_path: Path) -> list[Remediation]:
    return []


def _schema_message(config_path: Path) -> str:
    errors = CONFIG_VALIDATOR.validate(_INVALID_VERSION_CONFIG).errors
    return "\n".join(f"Repaired config failed validation: {e}" for e in errors)


class GenerateDefaultConfigFailureTests:
    @pytest.mark.parametrize(
        ("arrange", "repair", "error_code", "message", "remediations"),
        [
            pytest.param(
                _directory_target,
                False,
                ErrorCode.CONFIG_PATH_INVALID,
                lambda path: f"Config path '{path.as_posix()}' is a directory.",
                _none,
                id="path-is-directory",
            ),
            pytest.param(
                _missing_parent,
                False,
                ErrorCode.CONFIG_PARENT_MISSING,
                lambda path: f"Config directory '{path.parent.as_posix()}' does not exist.",
                _none,
                id="parent-missing",
            ),
            pytest.param(
                _unwritable_parent,
                False,
                ErrorCode.CONFIG_PATH_UNWRITABLE,
                lambda path: f"Config path '{path.as_posix()}' is not writable.",
                lambda path: [
                    Remediation(message=f"Ensure write permissions for '{path.parent.as_posix()}' directory")
                ],
                id="parent-unwritable",
            ),
            pytest.param(
                _invalid_json,
                True,
                ErrorCode.CONFIG_JSON_INVALID,
                lambda path: (
                    f"Invalid config JSON at '{path.as_posix()}': "
                    "Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"
                ),
                lambda path: _INVALID_JSON_REMEDIATIONS,
                id="json-invalid",
            ),
            pytest.param(
                _list_root,
                True,
                ErrorCode.CONFIG_ROOT_INVALID,
                lambda path: f"Invalid config JSON at '{path.as_posix()}': root must be a JSON object",
                _none,
                id="root-invalid",
            ),
            pytest.param(
                _schema_invalid, True, ErrorCode.CONFIG_SCHEMA_INVALID, _schema_message, _none, id="schema-invalid"
            ),
            pytest.param(
                _write_fails,
                False,
                ErrorCode.CONFIG_WRITE_FAILED,
                lambda path: f"Unable to write config at '{path.as_posix()}': {_WRITE_ERROR}",
                _none,
                id="write-failed",
            ),
        ],
    )
    def test_failure_raises_config_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        arrange: Callable[[Path, pytest.MonkeyPatch], Path],
        repair: bool,
        error_code: ErrorCode,
        message: Callable[[Path], str],
        remediations: Callable[[Path], list[Remediation]],
    ) -> None:
        """Raise ConfigError with the case's code, message and remediations, leaving config.json as it was."""
        config_path = arrange(tmp_path, monkeypatch).resolve()
        before = config_path.read_bytes() if config_path.is_file() else None

        with pytest.raises(ConfigError) as raised:
            generate_default_config(config_path, "demo", repair=repair)

        assert (raised.value.error_code, raised.value.message) == (error_code, message(config_path))
        assert list(raised.value.remediations) == remediations(config_path)
        assert (config_path.read_bytes() if config_path.is_file() else None) == before
        if error_code == ErrorCode.CONFIG_WRITE_FAILED:
            assert raised.value.__cause__ is _WRITE_ERROR
