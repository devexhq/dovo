"""Contract tests for dovo.core.bootstrap.services.initialize.initialize_workspace."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from dovo.common.constants import BOOTSTRAP_META_REL
from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import Filesystem
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.common.lock import LockTimeoutError
from dovo.common.models import Remediation
from dovo.core.bootstrap.models import BootstrapOutcome, InitFailureMode
from dovo.core.bootstrap.services.initialize import auto_initialize_workspace, initialize_workspace
from dovo.core.catalog.models import SeedResult
from dovo.core.config.generator import ConfigGenerationResult
from dovo.core.project.models import PROJECT_ID_REGEX, ProjectIdentity, ProjectIdentityOutcome
from dovo.core.project.services.identity import generate_project_identity, save_project_identity
from tests.harness.builders import WorkspaceBuilder, init_git_repository
from tests.harness.diagnostic_log import diagnostic_record, read_single_event


class InitializeWorkspaceGlobalHomeTests:
    @pytest.mark.parametrize(
        ("start", "with_git"),
        [
            pytest.param("home", False, id="home-without-git"),
            pytest.param("home", True, id="home-with-git"),
            pytest.param("global-home", False, id="path-is-global-home"),
            pytest.param("symlinked-home", True, id="symlinked-dot-dovo"),
        ],
    )
    def test_target_whose_dovo_is_global_home_is_refused_and_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, start: str, with_git: bool
    ) -> None:
        """Return ok False, PREFLIGHT, the exact global-home error and fix, and create no file under DOVO_HOME."""
        home = tmp_path / "home"
        if start == "symlinked-home":
            global_home = tmp_path / "data" / "dovo"
            global_home.mkdir(parents=True)
            home.mkdir()
            (home / ".dovo").symlink_to(global_home, target_is_directory=True)
        else:
            global_home = home / ".dovo"
            global_home.mkdir(parents=True)
        if with_git:
            init_git_repository(home)
        monkeypatch.setenv("DOVO_HOME", str(global_home))

        result = initialize_workspace(global_home if start == "global-home" else home)

        assert result.ok is False
        assert result.failure_mode == InitFailureMode.PREFLIGHT
        assert result.errors == [
            f"Cannot initialize a workspace at '{home.resolve()}': its .dovo directory is the global Dovo home "
            f"'{global_home.resolve()}'."
        ]
        assert result.remediations == [
            Remediation(message="Run 'dovo init' inside a project directory"),
            Remediation(message="Set 'DOVO_HOME' to a different directory."),
        ]
        assert list(global_home.iterdir()) == []


_IDENTITY_CASES: dict[str, tuple[str | None, bool, ProjectIdentityOutcome]] = {
    "fresh": (None, False, ProjectIdentityOutcome.CREATED),
    "rerun_preserves": (None, False, ProjectIdentityOutcome.PRESERVED),
    "id_and_force_overwrites": ("new-id", True, ProjectIdentityOutcome.OVERWRITTEN),
}


class InitializeWorkspaceTests:
    """Contract tests for initialize_workspace preflight and idempotency."""

    def test_initialize_workspace_outside_git_repo_sets_preflight_failure_mode(self, tmp_path: Path) -> None:
        result = initialize_workspace(tmp_path)

        assert result.ok is False
        assert result.failure_mode == InitFailureMode.PREFLIGHT
        assert result.bootstrap_result is None
        assert len(result.errors) == 1
        assert "not a valid Git repository" in result.errors[0]

    def test_initialize_workspace_outside_git_repo_creates_no_dovo_directory(self, tmp_path: Path) -> None:
        initialize_workspace(tmp_path)

        assert not (tmp_path / ".dovo").exists()

    def test_initialize_workspace_idempotent_rerun_preserves_custom_setting(self, tmp_path: Path) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_git().build()
        config_path = workspace / ".dovo" / "config.json"
        config_data = json.loads(config_path.read_text(encoding="utf-8"))
        config_data["project"]["name"] = "custom-project-name"
        config_path.write_text(json.dumps(config_data), encoding="utf-8")

        result = initialize_workspace(workspace)

        assert result.ok is True
        assert result.bootstrap_result is not None
        assert result.bootstrap_result.outcome == BootstrapOutcome.ALREADY_INITIALIZED
        persisted = json.loads(config_path.read_text(encoding="utf-8"))
        assert persisted["project"]["name"] == "custom-project-name"

    @pytest.mark.parametrize("case", ["fresh", "rerun_preserves", "id_and_force_overwrites"])
    def test_initialize_workspace_provisions_identity_fields(self, git_repo: Path, case: str) -> None:
        """Set project_identity (id on disk), identity_path and identity_outcome CREATED, PRESERVED or OVERWRITTEN."""
        project_id, force, expected_outcome = _IDENTITY_CASES[case]
        if case != "fresh":
            initialize_workspace(git_repo)

        result = initialize_workspace(git_repo, project_id=project_id, force=force)

        identity_path = Filesystem(git_repo).dovo_dir / "project.json"
        on_disk = ProjectIdentity.model_validate_json(identity_path.read_text(encoding="utf-8"))
        assert result.ok is True
        assert result.project_identity == on_disk
        assert result.identity_path == identity_path
        assert result.identity_outcome == expected_outcome
        assert project_id is None or on_disk.id == project_id

    def test_initialize_workspace_invalid_project_id_sets_invalid_project_id_failure_mode(self, git_repo: Path) -> None:
        result = initialize_workspace(git_repo, project_id="Bad Id!")

        assert result.ok is False
        assert result.failure_mode == InitFailureMode.INVALID_PROJECT_ID
        assert result.error_code == ErrorCode.PROJECT_ID_INVALID
        assert result.errors == ["Invalid project ID: must match ^[a-z0-9][a-z0-9-_]{2,62}$"]
        assert result.remediations == [
            Remediation(message=f"Pass a valid '--id' matching '{PROJECT_ID_REGEX}', or omit '--id' to generate one.")
        ]
        assert result.identity_path == Filesystem(git_repo).dovo_dir / "project.json"
        assert result.project_identity is None
        assert result.identity_outcome is None
        assert result.config_result is None
        assert not (git_repo / ".dovo" / "project.json").exists()

    def test_initialize_workspace_fresh_repo_creates_only_meta_subdir(self, git_repo: Path) -> None:
        result = initialize_workspace(git_repo)

        assert result.bootstrap_result is not None
        assert result.bootstrap_result.dirs_created == [result.bootstrap_result.root_path / ".meta"]
        for legacy_dir in ("sessions", "artifacts", "tmp", "logs"):
            assert not (result.bootstrap_result.root_path / legacy_dir).exists()

    def test_initialize_workspace_rerun_preserves_legacy_runtime_dirs_and_contents(self, git_repo: Path) -> None:
        initialize_workspace(git_repo)
        legacy_file = git_repo / ".dovo" / "sessions" / "existing.json"
        legacy_file.parent.mkdir(parents=True, exist_ok=True)
        legacy_file.write_text('{"a": 1}', encoding="utf-8")

        initialize_workspace(git_repo)

        assert legacy_file.read_text(encoding="utf-8") == '{"a": 1}'

    def test_initialize_workspace_builds_paths_from_provisioned_identity_without_rereading_it(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        def _unexpected_load(*args: object, **kwargs: object) -> None:
            raise AssertionError("identity must not be re-read")

        monkeypatch.setattr("dovo.core.project.services.storage.load_project_identity", _unexpected_load)

        result = initialize_workspace(git_repo)

        assert result.ok is True
        assert (resolve_global_paths(None).data_dir / "dovo.db").is_file()


def _fail_seeding(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the seeding boundary so starter-blueprint seeding reports the error "boom"."""
    monkeypatch.setattr(
        "dovo.core.bootstrap.services.initialize.seed_all_catalog_templates",
        lambda _paths: SeedResult(errors=["boom"]),
    )


def _tree_bytes(root: Path) -> dict[str, bytes | None]:
    """Map every path under root (relative, as a string) to its bytes, or None for a directory."""
    return {
        str(path.relative_to(root)): None if path.is_dir() else path.read_bytes() for path in sorted(root.rglob("*"))
    }


def _fail_identity_io(monkeypatch: pytest.MonkeyPatch, repo: Path, failure: str) -> None:
    """Make provisioning repo's project.json fail as failure names, touching no other file."""
    identity_path = Filesystem(repo).dovo_dir / "project.json"
    if failure == "invalid_schema":
        identity_path.parent.mkdir(parents=True, exist_ok=True)
        identity_path.write_text("{not-json", encoding="utf-8")
    elif failure == "unreadable":
        save_project_identity(identity_path, generate_project_identity("existing-id"))
        real_read_text = Path.read_text

        def _denied_read(
            self: Path, encoding: str | None = None, errors: str | None = None, newline: str | None = None
        ) -> str:
            if self.name == "project.json":
                raise PermissionError("denied")
            return real_read_text(self, encoding=encoding, errors=errors, newline=newline)

        monkeypatch.setattr(Path, "read_text", _denied_read)
    elif failure == "write_failed":
        real_write = Filesystem.atomic_write_json

        def _full_disk(path: Path, data: dict[str, object], *, mode: int | None = None) -> None:
            if path.name == "project.json":
                raise OSError("disk full")
            real_write(path, data, mode=mode)

        monkeypatch.setattr("dovo.core.project.services.identity.Filesystem.atomic_write_json", _full_disk)
    elif failure == "locked":
        monkeypatch.setattr(
            "dovo.core.project.services.identity.WorkspaceLock.__enter__",
            MagicMock(side_effect=LockTimeoutError("lock held")),
        )
    else:

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise TypeError("boom")

        monkeypatch.setattr("dovo.core.project.services.identity.generate_project_identity", _boom)


def _expected_identity_message(identity_path: Path, failure: str) -> str:
    """Return the identity error message failure produces, before any Details logged line."""
    if failure == "invalid_schema":
        with pytest.raises(ValidationError) as validation:
            ProjectIdentity.model_validate_json("{not-json")
        return f"Project identity at '{identity_path}' is invalid: {validation.value}."
    if failure == "unreadable":
        return f"Unable to read project identity at '{identity_path}': denied."
    if failure == "write_failed":
        return f"Unable to write project identity at '{identity_path}': disk full."
    return f"Unable to acquire project identity lock at '{identity_path}': lock held."


class InitializeIdentityFailureTests:
    """Contract tests for identity failures caught once in initialize_workspace."""

    @pytest.mark.parametrize(
        ("failure", "error_code", "logged"),
        [
            pytest.param("invalid_schema", ErrorCode.PROJECT_IDENTITY_SCHEMA_INVALID, False, id="invalid_schema"),
            pytest.param("unreadable", ErrorCode.PROJECT_IDENTITY_UNREADABLE, True, id="unreadable"),
            pytest.param("write_failed", ErrorCode.PROJECT_IDENTITY_WRITE_FAILED, True, id="write_failed"),
            pytest.param("locked", ErrorCode.PROJECT_IDENTITY_LOCKED, False, id="locked"),
        ],
    )
    def test_identity_domain_failure_is_reported_as_bootstrap(
        self,
        git_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        frozen_diagnostic_clock: Path,
        failure: str,
        error_code: ErrorCode,
        logged: bool,
    ) -> None:
        """Return a BOOTSTRAP failure with the identity error code, its message alone and its record in diagnostics when one was logged."""
        identity_path = Filesystem(git_repo).dovo_dir / "project.json"
        _fail_identity_io(monkeypatch, git_repo, failure)

        result = initialize_workspace(git_repo)

        assert result.ok is False
        assert result.failure_mode == InitFailureMode.BOOTSTRAP
        assert result.error_code == error_code
        assert result.errors == [_expected_identity_message(identity_path, failure)]
        assert result.diagnostics == ([diagnostic_record(frozen_diagnostic_clock)] if logged else [])
        assert result.remediations == [Remediation(message="Resolve the path conflict above, then rerun 'dovo init'")]
        assert result.identity_path == identity_path
        assert result.project_identity is None
        assert result.identity_outcome is None
        assert result.config_result is None
        assert result.seed_result is None
        assert frozen_diagnostic_clock.exists() is logged
        assert not (git_repo / ".dovo" / "config.json").exists()

    def test_unexpected_provisioning_error_is_internal_error(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch, frozen_diagnostic_clock: Path
    ) -> None:
        """Return an INTERNAL_ERROR BOOTSTRAP failure naming the operation, with its record in diagnostics, and record one event for it."""
        identity_path = Filesystem(git_repo).dovo_dir / "project.json"
        _fail_identity_io(monkeypatch, git_repo, "unexpected")

        result = initialize_workspace(git_repo)

        assert result.ok is False
        assert result.failure_mode == InitFailureMode.BOOTSTRAP
        assert result.error_code == ErrorCode.INTERNAL_ERROR
        assert result.errors == ["Unexpected error during bootstrap.provision_identity: boom"]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        assert result.remediations == [Remediation(message="Resolve the path conflict above, then rerun 'dovo init'")]
        assert result.identity_path == identity_path
        event = read_single_event(frozen_diagnostic_clock)
        assert event["operation"] == "bootstrap.provision_identity"
        assert event["exception_type"] == "TypeError"

    def test_overwrite_repair_rerun_orders_identity_warning_first(self, git_repo: Path) -> None:
        """Return the preserve warning with the existing id, then the config warning."""
        first = initialize_workspace(git_repo)
        assert first.project_identity is not None

        result = initialize_workspace(git_repo, overwrite=True, repair=True)

        assert result.ok is True
        assert result.warnings == [
            f"Project identity already exists (id={first.project_identity.id}); preserving existing identity. "
            "Rerun with --id <new-id> --force to replace it.",
            "repair ignored when overwrite=True",
        ]


class AutoInitializeWorkspaceTests:
    """Service tests for auto_initialize_workspace's rollback contract."""

    def test_auto_initialize_workspace_success_returns_initialize_result_unchanged(self, git_repo: Path) -> None:
        """In a fresh Git repository the result is ok and project.json, config.json and .meta/bootstrap.json exist."""
        result = auto_initialize_workspace(git_repo)

        assert result.ok is True
        assert (git_repo / ".dovo" / "project.json").is_file()
        assert (git_repo / ".dovo" / "config.json").is_file()
        assert (git_repo / ".dovo" / BOOTSTRAP_META_REL).is_file()

    @pytest.mark.parametrize(
        ("case", "failure_mode"),
        [
            pytest.param("non-git", InitFailureMode.PREFLIGHT, id="non-git"),
            pytest.param("global-home", InitFailureMode.PREFLIGHT, id="global-home"),
        ],
    )
    def test_auto_initialize_workspace_preflight_failure_is_returned_without_rollback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, failure_mode: InitFailureMode
    ) -> None:
        """Return the preflight result with its errors unchanged and leave the target tree byte-for-byte unchanged."""
        target = tmp_path / "target"
        target.mkdir()
        if case == "global-home":
            (target / ".dovo").mkdir()
            (target / ".dovo" / "sentinel.txt").write_text("keep\n", encoding="utf-8")
            init_git_repository(target)
            monkeypatch.setenv("DOVO_HOME", str(target / ".dovo"))
        before = _tree_bytes(target)
        expected = initialize_workspace(target)

        result = auto_initialize_workspace(target)

        assert result.failure_mode == failure_mode
        assert result.errors == expected.errors
        assert _tree_bytes(target) == before

    @pytest.mark.parametrize(
        ("patched", "failure_mode", "error"),
        [
            pytest.param("generate_default_config", InitFailureMode.CONFIG_GENERATION, "bad config", id="config"),
            pytest.param("seed_all_catalog_templates", None, "boom", id="seed"),
        ],
    )
    def test_auto_initialize_workspace_failure_after_preflight_removes_new_dovo_dir(
        self,
        git_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        patched: str,
        failure_mode: InitFailureMode | None,
        error: str,
    ) -> None:
        """Return the failed result with its failure_mode and patched error, and remove the new .dovo/."""
        failure = (
            ConfigGenerationResult(errors=[error])
            if patched == "generate_default_config"
            else SeedResult(errors=[error])
        )
        monkeypatch.setattr(f"dovo.core.bootstrap.services.initialize.{patched}", lambda *_args, **_kwargs: failure)

        result = auto_initialize_workspace(git_repo)

        assert result.ok is False
        assert result.failure_mode == failure_mode
        assert result.errors == [error]
        assert not (git_repo / ".dovo").exists()

    def test_auto_initialize_workspace_failure_restores_preexisting_dovo_dir(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Keep a pre-existing user file and .gitignore bytes, and remove everything the failed attempt created."""
        dovo_dir = git_repo / ".dovo"
        dovo_dir.mkdir()
        (dovo_dir / "notes.txt").write_text("mine\n", encoding="utf-8")
        (dovo_dir / ".gitignore").write_text("custom\n", encoding="utf-8")
        _fail_seeding(monkeypatch)

        result = auto_initialize_workspace(git_repo)

        assert result.ok is False
        assert (dovo_dir / "notes.txt").read_text(encoding="utf-8") == "mine\n"
        assert (dovo_dir / ".gitignore").read_bytes() == b"custom\n"
        for created in ("project.json", "config.json", ".meta", "catalog"):
            assert not (dovo_dir / created).exists()

    def test_auto_initialize_workspace_reports_rollback_failure_in_errors(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Append the rollback error to the seed error when removing the new .dovo/ fails."""
        _fail_seeding(monkeypatch)

        def _denied(_path: object) -> None:
            raise OSError("denied")

        monkeypatch.setattr("dovo.core.bootstrap.services.rollback.shutil.rmtree", _denied)

        result = auto_initialize_workspace(git_repo)

        dovo_dir = (git_repo / ".dovo").resolve()
        assert result.errors == [
            "boom",
            f"Could not roll back the partial workspace at {dovo_dir.as_posix()}: denied. Remove it, then rerun dovo run.",
        ]

    def test_auto_initialize_workspace_unexpected_provisioning_error_rolls_back(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return the INTERNAL_ERROR failure instead of raising, and remove the new .dovo/."""
        _fail_identity_io(monkeypatch, git_repo, "unexpected")

        result = auto_initialize_workspace(git_repo)

        assert result.ok is False
        assert result.failure_mode == InitFailureMode.BOOTSTRAP
        assert result.error_code == ErrorCode.INTERNAL_ERROR
        assert result.errors[0].startswith("Unexpected error during bootstrap.provision_identity: boom")
        assert not (git_repo / ".dovo").exists()

    def test_auto_initialize_workspace_raised_failure_removes_new_dovo_dir_and_reraises(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Re-raise an exception from database initialization unchanged and remove the new .dovo/."""

        def _unwritable(_path: object) -> None:
            raise PermissionError("unwritable data dir")

        monkeypatch.setattr("dovo.core.bootstrap.services.initialize.init_database", _unwritable)

        with pytest.raises(PermissionError, match=r"^unwritable data dir$"):
            auto_initialize_workspace(git_repo)

        assert not (git_repo / ".dovo").exists()

    def test_auto_initialize_workspace_raised_failure_notes_rollback_failure(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Re-raise the database exception with the rollback error as its only note when removing .dovo/ fails."""

        def _unwritable(_path: object) -> None:
            raise PermissionError("unwritable data dir")

        def _denied(_path: object) -> None:
            raise OSError("denied")

        monkeypatch.setattr("dovo.core.bootstrap.services.initialize.init_database", _unwritable)
        monkeypatch.setattr("dovo.core.bootstrap.services.rollback.shutil.rmtree", _denied)

        with pytest.raises(PermissionError) as exc_info:
            auto_initialize_workspace(git_repo)

        dovo_dir = (git_repo / ".dovo").resolve()
        assert str(exc_info.value) == "unwritable data dir"
        assert exc_info.value.__notes__ == [
            f"Could not roll back the partial workspace at {dovo_dir.as_posix()}: denied. Remove it, then rerun dovo run."
        ]


class InitializeBootstrapRemediationTests:
    """Contract tests for the remediation a bootstrap failure carries."""

    def test_bootstrap_failure_carries_rerun_remediation(self, tmp_path: Path) -> None:
        """Return failure_mode BOOTSTRAP with remediations == [Remediation(message="Resolve the path conflict above, then rerun 'dovo init'")]."""
        init_git_repository(tmp_path)
        (tmp_path / ".dovo").write_text("not a directory", encoding="utf-8")

        result = initialize_workspace(tmp_path)

        assert result.ok is False
        assert result.failure_mode == InitFailureMode.BOOTSTRAP
        assert result.remediations == [Remediation(message="Resolve the path conflict above, then rerun 'dovo init'")]
