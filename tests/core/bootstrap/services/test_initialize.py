"""Contract tests for dovo.core.bootstrap.services.initialize.initialize_workspace."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from dovo.common.constants import BOOTSTRAP_META_REL, DOVO_GITIGNORE_CONTENT
from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import Filesystem
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.common.filesystem.services.paths import get_catalog_templates_dir
from dovo.common.lock import LockTimeoutError, WorkspaceLock
from dovo.common.models import Remediation
from dovo.common.utils import display_path
from dovo.core.bootstrap.models import BootstrapOutcome, BootstrapSummary
from dovo.core.bootstrap.services.initialize import auto_initialize_workspace, initialize_workspace
from dovo.core.catalog.exceptions import CatalogError
from dovo.core.catalog.models import SeedSummary
from dovo.core.config.exceptions import ConfigError
from dovo.core.config.generator import build_default_config
from dovo.core.config.models import ConfigGenerationSummary
from dovo.core.project.models import PROJECT_ID_REGEX, ProjectIdentity, ProjectIdentityOutcome
from dovo.core.project.services.identity import generate_project_identity, save_project_identity
from tests.harness.builders import WorkspaceBuilder, init_git_repository
from tests.harness.diagnostic_log import diagnostic_record, read_single_event
from tests.harness.lock import raise_lock_timeout


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
        """Return ok False, BOOTSTRAP_TARGET_FORBIDDEN, the exact global-home error and fix, and create no file under DOVO_HOME."""
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
        assert result.error_code == ErrorCode.BOOTSTRAP_TARGET_FORBIDDEN
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

    def test_success_holds_every_summary(self, git_repo: Path) -> None:
        result = initialize_workspace(git_repo)

        assert result.error_code is None
        assert isinstance(result.bootstrap, BootstrapSummary)
        assert isinstance(result.config_generation, ConfigGenerationSummary)
        assert isinstance(result.seed, SeedSummary)

    def test_fresh_init_writes_the_same_files_as_main(self, git_repo: Path) -> None:
        """Write the default config, the .gitignore, the bootstrap metadata and the packaged templates unchanged."""
        result = initialize_workspace(git_repo)

        dovo_dir = Filesystem(git_repo).dovo_dir
        config = json.loads((dovo_dir / "config.json").read_text(encoding="utf-8"))
        expected = build_default_config(Filesystem(git_repo).root_dir.name)
        expected["project"]["initialized_at"] = config["project"]["initialized_at"]
        assert config == expected
        assert (dovo_dir / ".gitignore").read_text(encoding="utf-8") == DOVO_GITIGNORE_CONTENT
        metadata = json.loads((dovo_dir / BOOTSTRAP_META_REL).read_text(encoding="utf-8"))
        assert set(metadata) == {
            "schema_version",
            "initialized_at",
            "last_checked_at",
            "tool_version",
            "status",
            "root_path",
        }
        assert metadata["status"] == "initialized"
        assert result.seed is not None
        assert result.seed.created_files
        templates = Path(str(get_catalog_templates_dir()))
        for created in result.seed.created_files:
            assert created.read_bytes() == (templates / created.relative_to(created.parents[2])).read_bytes()

    def test_outside_git_repo_reports_repo_not_found(self, tmp_path: Path) -> None:
        result = initialize_workspace(tmp_path)

        assert result.ok is False
        assert result.error_code == ErrorCode.BOOTSTRAP_REPO_NOT_FOUND
        assert result.bootstrap is None
        assert result.errors == [
            "The current directory is not a valid Git repository.\n"
            "Run [bold cyan]git init[/bold cyan] before running [bold cyan]dovo init[/bold cyan]."
        ]
        assert result.remediations == []

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
        assert result.bootstrap is not None
        assert result.bootstrap.outcome == BootstrapOutcome.ALREADY_INITIALIZED
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

    def test_invalid_project_id_reports_project_id_invalid(self, git_repo: Path) -> None:
        """Return PROJECT_ID_INVALID with the identity error's remediations before anything is written."""
        result = initialize_workspace(git_repo, project_id="Bad Id!")

        assert result.ok is False
        assert result.error_code == ErrorCode.PROJECT_ID_INVALID
        assert result.errors == ["Invalid project ID: must match ^[a-z0-9][a-z0-9-_]{2,62}$"]
        assert result.remediations == [
            Remediation(message=f"Pass a valid '--id' matching '{PROJECT_ID_REGEX}'."),
            Remediation(
                message="Run 'dovo init' without '--id' to keep the existing project ID or generate a new one.",
                command="dovo init",
            ),
        ]
        assert result.bootstrap is None
        assert result.identity_path is None
        assert result.project_identity is None
        assert result.identity_outcome is None
        assert result.config_generation is None
        assert not (git_repo / ".dovo").exists()

    def test_initialize_workspace_fresh_repo_creates_only_meta_subdir(self, git_repo: Path) -> None:
        result = initialize_workspace(git_repo)

        assert result.bootstrap is not None
        assert result.bootstrap.dirs_created == (result.bootstrap.root_path / ".meta",)
        for legacy_dir in ("sessions", "artifacts", "tmp", "logs"):
            assert not (result.bootstrap.root_path / legacy_dir).exists()

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


def _raise(error: Exception) -> Callable[..., None]:
    """Return a callable that raises error, to patch an initialization step with."""

    def _raising(*_args: object, **_kwargs: object) -> None:
        raise error

    return _raising


def _fail_seeding(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the seeding boundary so starter-blueprint seeding raises CATALOG_SEED_FAILED "boom"."""
    monkeypatch.setattr(
        "dovo.core.bootstrap.services.initialize.seed_all_catalog_templates",
        _raise(CatalogError(ErrorCode.CATALOG_SEED_FAILED, "boom")),
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
    else:
        real_write = Filesystem.atomic_write_json

        def _full_disk(path: Path, data: dict[str, object], *, mode: int | None = None) -> None:
            if path.name == "project.json":
                raise OSError("disk full")
            real_write(path, data, mode=mode)

        monkeypatch.setattr("dovo.core.project.services.identity.Filesystem.atomic_write_json", _full_disk)


def _expected_identity_message(identity_path: Path, failure: str) -> str:
    """Return the identity error message failure produces, before any Details logged line."""
    if failure == "invalid_schema":
        with pytest.raises(ValidationError) as validation:
            ProjectIdentity.model_validate_json("{not-json")
        return f"Project identity at '{identity_path}' is invalid: {validation.value}."
    if failure == "unreadable":
        return f"Unable to read project identity at '{identity_path}': denied."
    return f"Unable to write project identity at '{identity_path}': disk full."


class InitializeIdentityFailureTests:
    """Contract tests for identity failures caught once in initialize_workspace."""

    @pytest.mark.parametrize(
        ("failure", "error_code", "logged"),
        [
            pytest.param("invalid_schema", ErrorCode.PROJECT_IDENTITY_SCHEMA_INVALID, False, id="invalid_schema"),
            pytest.param("unreadable", ErrorCode.PROJECT_IDENTITY_UNREADABLE, True, id="unreadable"),
            pytest.param("write_failed", ErrorCode.PROJECT_IDENTITY_WRITE_FAILED, True, id="write_failed"),
        ],
    )
    def test_identity_domain_failure_reports_its_own_remediations(
        self,
        git_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        frozen_diagnostic_clock: Path,
        failure: str,
        error_code: ErrorCode,
        logged: bool,
    ) -> None:
        """Return the identity error code, its message alone, its own remediations and its record in diagnostics when one was logged."""
        identity_path = Filesystem(git_repo).dovo_dir / "project.json"
        _fail_identity_io(monkeypatch, git_repo, failure)

        result = initialize_workspace(git_repo)

        assert result.ok is False
        assert result.error_code == error_code
        assert result.errors == [_expected_identity_message(identity_path, failure)]
        assert result.diagnostics == ([diagnostic_record(frozen_diagnostic_clock)] if logged else [])
        assert result.remediations == []
        assert result.bootstrap is not None
        assert result.identity_path == identity_path
        assert result.project_identity is None
        assert result.identity_outcome is None
        assert result.config_generation is None
        assert result.seed is None
        assert frozen_diagnostic_clock.exists() is logged
        assert not (git_repo / ".dovo" / "config.json").exists()

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


_RERUN_REMEDIATION = Remediation(message="Resolve the path conflict above, then rerun 'dovo init'")


def _dovo_is_file(repo: Path) -> tuple[ErrorCode, str, list[Remediation], int]:
    dovo_dir = Filesystem(repo).dovo_dir
    dovo_dir.write_text("not a directory", encoding="utf-8")
    message = f"{display_path(dovo_dir.resolve())} exists but is a file, not a directory."
    return ErrorCode.BOOTSTRAP_PATH_INVALID, message, [_RERUN_REMEDIATION], 0


def _config_is_directory(repo: Path) -> tuple[ErrorCode, str, list[Remediation], int]:
    config_file = Filesystem(repo).config_file
    config_file.mkdir(parents=True)
    return ErrorCode.CONFIG_PATH_INVALID, f"Config path '{config_file.resolve().as_posix()}' is a directory.", [], 2


def _seed_target_is_directory(repo: Path) -> tuple[ErrorCode, str, list[Remediation], int]:
    blocked = Filesystem(repo).dovo_dir / "catalog" / "blueprints" / "dovo" / "fix-tests.yml"
    blocked.mkdir(parents=True)
    message = (
        "Could not seed the starter catalog templates:\n"
        f"{display_path(blocked.resolve())} exists as a directory, not a file."
    )
    return ErrorCode.CATALOG_SEED_FAILED, message, [], 3


class InitializeStepFailureTests:
    """Contract tests for a bootstrap, config or seeding failure caught once in initialize_workspace."""

    @pytest.mark.parametrize(
        "arrange",
        [
            pytest.param(_dovo_is_file, id="bootstrap"),
            pytest.param(_config_is_directory, id="config"),
            pytest.param(_seed_target_is_directory, id="seed"),
        ],
    )
    def test_step_domain_failure_is_reported_once(
        self, git_repo: Path, arrange: Callable[[Path], tuple[ErrorCode, str, list[Remediation], int]]
    ) -> None:
        """Copy the error's code, message and remediations, keep every earlier step's summary, and leave later ones None."""
        error_code, message, remediations, completed = arrange(git_repo)

        result = initialize_workspace(git_repo)

        assert (result.error_code, result.errors, result.remediations) == (error_code, [message], remediations)
        steps = [result.bootstrap, result.project_identity, result.config_generation, result.seed]
        assert [step is not None for step in steps] == [index < completed for index in range(4)]
        assert (resolve_global_paths().data_dir / "dovo.db").exists() is (completed == 3)

    @pytest.mark.parametrize(
        "step",
        [
            "bootstrap_dovo",
            "provision_project_identity",
            "init_database",
            "generate_default_config",
            "seed_all_catalog_templates",
        ],
    )
    def test_unexpected_step_error_is_internal_error(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch, frozen_diagnostic_clock: Path, step: str
    ) -> None:
        """Return INTERNAL_ERROR naming the operation, with one record in diagnostics, whichever step raised."""
        monkeypatch.setattr(f"dovo.core.bootstrap.services.initialize.{step}", _raise(TypeError("boom")))

        result = initialize_workspace(git_repo)

        assert result.error_code == ErrorCode.INTERNAL_ERROR
        assert result.errors == ["Unexpected error during bootstrap.initialize: boom"]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert (event["operation"], event["exception_type"]) == ("bootstrap.initialize", "TypeError")

    def test_config_write_os_error_is_recorded_in_diagnostics(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch, frozen_diagnostic_clock: Path
    ) -> None:
        """Return CONFIG_WRITE_FAILED with the OSError's record in diagnostics and no Details logged text."""
        real_write = Filesystem.atomic_write_json

        def _denied(path: Path, data: dict[str, object], *, mode: int | None = None) -> None:
            if path.name == "config.json":
                raise PermissionError("denied")
            real_write(path, data, mode=mode)

        monkeypatch.setattr(Filesystem, "atomic_write_json", staticmethod(_denied))

        result = initialize_workspace(git_repo)

        assert result.error_code == ErrorCode.CONFIG_WRITE_FAILED
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        assert not any("Details logged:" in error for error in result.errors)


class InitializeIdentityLockTimeoutTests:
    """Contract tests for a workspace lock timeout while provisioning the project identity."""

    def test_initialize_workspace_identity_lock_timeout_propagates_and_keeps_bootstrap_tree(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Raise LockTimeoutError("lock held") unchanged, leave <DOVO_HOME>/logs absent, and keep the bootstrapped .dovo/ with no project.json or config.json."""
        monkeypatch.setattr(WorkspaceLock, "__enter__", raise_lock_timeout)

        with pytest.raises(LockTimeoutError, match=r"^lock held$"):
            initialize_workspace(git_repo)

        assert not resolve_global_paths().logs_dir.exists()
        dovo_dir = git_repo / ".dovo"
        assert (dovo_dir / ".gitignore").is_file()
        assert (dovo_dir / BOOTSTRAP_META_REL).is_file()
        assert not (dovo_dir / "project.json").exists()
        assert not (dovo_dir / "config.json").exists()

    def test_auto_initialize_workspace_identity_lock_timeout_restores_preexisting_dovo_dir(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Re-raise LockTimeoutError("lock held") with no note and restore a pre-existing .dovo/ byte for byte."""
        dovo_dir = git_repo / ".dovo"
        dovo_dir.mkdir()
        (dovo_dir / "notes.txt").write_text("mine\n", encoding="utf-8")
        before = _tree_bytes(dovo_dir)
        monkeypatch.setattr(WorkspaceLock, "__enter__", raise_lock_timeout)

        with pytest.raises(LockTimeoutError, match=r"^lock held$") as raised:
            auto_initialize_workspace(git_repo)

        assert getattr(raised.value, "__notes__", []) == []
        assert _tree_bytes(dovo_dir) == before


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
        ("case", "error_code"),
        [
            pytest.param("non-git", ErrorCode.BOOTSTRAP_REPO_NOT_FOUND, id="non-git"),
            pytest.param("global-home", ErrorCode.BOOTSTRAP_TARGET_FORBIDDEN, id="global-home"),
        ],
    )
    def test_auto_initialize_workspace_preflight_failure_is_returned_without_rollback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, error_code: ErrorCode
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

        assert result.error_code == error_code
        assert result.errors == expected.errors
        assert _tree_bytes(target) == before

    @pytest.mark.parametrize(
        ("patched", "error"),
        [
            pytest.param(
                "generate_default_config", ConfigError(ErrorCode.CONFIG_WRITE_FAILED, "bad config"), id="config"
            ),
            pytest.param("seed_all_catalog_templates", CatalogError(ErrorCode.CATALOG_SEED_FAILED, "boom"), id="seed"),
        ],
    )
    def test_auto_initialize_workspace_failure_after_preflight_removes_new_dovo_dir(
        self,
        git_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        patched: str,
        error: ConfigError | CatalogError,
    ) -> None:
        """Return the failed result with the raised error's code and message, and remove the new .dovo/."""
        monkeypatch.setattr(f"dovo.core.bootstrap.services.initialize.{patched}", _raise(error))

        result = auto_initialize_workspace(git_repo)

        assert result.ok is False
        assert result.error_code == error.error_code
        assert result.errors == [error.message]
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

    def test_auto_initialize_workspace_unexpected_step_error_rolls_back(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return the INTERNAL_ERROR failure of a database error instead of raising, and remove the new .dovo/."""
        monkeypatch.setattr(
            "dovo.core.bootstrap.services.initialize.init_database", _raise(PermissionError("unwritable data dir"))
        )

        result = auto_initialize_workspace(git_repo)

        assert result.ok is False
        assert result.error_code == ErrorCode.INTERNAL_ERROR
        assert result.errors == ["Unexpected error during bootstrap.initialize: unwritable data dir"]
        assert not (git_repo / ".dovo").exists()

    def test_auto_initialize_workspace_raised_failure_removes_new_dovo_dir_and_reraises(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Re-raise a lock timeout unchanged and remove the new .dovo/."""
        monkeypatch.setattr(WorkspaceLock, "__enter__", raise_lock_timeout)

        with pytest.raises(LockTimeoutError, match=r"^lock held$"):
            auto_initialize_workspace(git_repo)

        assert not (git_repo / ".dovo").exists()

    def test_auto_initialize_workspace_raised_failure_notes_rollback_failure(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Re-raise the lock timeout with the rollback error as its only note when removing .dovo/ fails."""

        def _denied(_path: object) -> None:
            raise OSError("denied")

        monkeypatch.setattr(WorkspaceLock, "__enter__", raise_lock_timeout)
        monkeypatch.setattr("dovo.core.bootstrap.services.rollback.shutil.rmtree", _denied)

        with pytest.raises(LockTimeoutError) as exc_info:
            auto_initialize_workspace(git_repo)

        dovo_dir = (git_repo / ".dovo").resolve()
        assert str(exc_info.value) == "lock held"
        assert exc_info.value.__notes__ == [
            f"Could not roll back the partial workspace at {dovo_dir.as_posix()}: denied. Remove it, then rerun dovo run."
        ]
