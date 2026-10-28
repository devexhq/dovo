"""Unit tests for dovo.core.project.services.identity."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from freezegun import freeze_time
from pydantic import ValidationError

from dovo.common.error_codes import ErrorCode
from dovo.common.lock import LockTimeoutError, WorkspaceLock
from dovo.common.models import Remediation
from dovo.core.project.exceptions import ProjectIdentityError
from dovo.core.project.models import PROJECT_ID_REGEX, ProjectIdentity, ProjectIdentityOutcome
from dovo.core.project.services import identity as identity_service
from dovo.core.project.services.identity import (
    generate_project_identity,
    load_project_identity,
    provision_project_identity,
    save_project_identity,
    validate_project_id,
)
from tests.harness.lock import raise_lock_timeout

UTC_TIMESTAMP = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _write_identity(path: Path, identity: ProjectIdentity) -> None:
    path.write_text(identity.model_dump_json(), encoding="utf-8")


def _fail_atomic_write(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_write_error(_path: Path, _data: dict[str, object]) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(identity_service.Filesystem, "atomic_write_json", raise_write_error)


class ProjectIdentityServiceTests:
    """Unit tests for project identity services."""

    @freeze_time(UTC_TIMESTAMP)
    def test_generate_project_identity_with_explicit_values_returns_utc_identity(self) -> None:
        identity = generate_project_identity("project-624", "Project 624")

        assert identity.id == "project-624"
        assert identity.display_name == "Project 624"
        assert identity.created_at == UTC_TIMESTAMP

    def test_generate_project_identity_without_id_uses_generated_slug(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(identity_service, "generate_slug", lambda: "calm-otter")

        identity = generate_project_identity(display_name="Generated Project")

        assert identity.id == "calm-otter"
        assert identity.created_at.utcoffset() == UTC_TIMESTAMP.utcoffset()

    def test_load_project_identity_valid_json_returns_identity(self, tmp_path: Path) -> None:
        path = tmp_path / "project.json"
        identity = ProjectIdentity(id="project-624", display_name="Project 624", created_at=UTC_TIMESTAMP)
        _write_identity(path, identity)

        assert load_project_identity(path) == identity

    def test_load_project_identity_missing_path_raises_not_found(self, tmp_path: Path) -> None:
        path = tmp_path / "project.json"

        with pytest.raises(ProjectIdentityError) as raised:
            load_project_identity(path)

        assert raised.value.error_code == ErrorCode.PROJECT_IDENTITY_NOT_FOUND
        assert raised.value.message == f"Project identity file not found at '{path}'."
        assert raised.value.remediations == ()

    @pytest.mark.parametrize(
        "contents",
        [
            pytest.param("{not-json", id="malformed_json"),
            pytest.param('{"id": "Bad", "created_at": "2026-01-02T03:04:05Z"}', id="invalid_identity"),
        ],
    )
    def test_load_project_identity_invalid_content_raises_schema_invalid(self, tmp_path: Path, contents: str) -> None:
        path = tmp_path / "project.json"
        path.write_text(contents, encoding="utf-8")
        with pytest.raises(ValidationError) as validation:
            ProjectIdentity.model_validate_json(contents)

        with pytest.raises(ProjectIdentityError) as raised:
            load_project_identity(path)

        assert raised.value.error_code == ErrorCode.PROJECT_IDENTITY_SCHEMA_INVALID
        assert raised.value.message == (f"Project identity at '{path}' is invalid: {validation.value}.")
        assert raised.value.remediations == ()

    @pytest.mark.parametrize("failure", ["oserror", "invalid_utf8"])
    def test_load_project_identity_unreadable_raises_unreadable_from_cause(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
    ) -> None:
        path = tmp_path / "project.json"
        if failure == "oserror":
            path.write_text("{}", encoding="utf-8")
            cause: Exception = OSError("permission denied")

            def raise_read_error(_path: Path, *args: object, **kwargs: object) -> str:
                raise cause

            monkeypatch.setattr(Path, "read_text", raise_read_error)
            expected_type: type[Exception] = OSError
        else:
            path.write_bytes(b"\xff\xfe\x00invalid")
            expected_type = UnicodeDecodeError

        with pytest.raises(ProjectIdentityError) as raised:
            load_project_identity(path)

        assert raised.value.error_code == ErrorCode.PROJECT_IDENTITY_UNREADABLE
        assert type(raised.value.__cause__) is expected_type
        assert raised.value.message == (f"Unable to read project identity at '{path}': {raised.value.__cause__}.")
        assert raised.value.remediations == ()

    def test_save_project_identity_writes_indented_json_and_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "project.json"
        identity = ProjectIdentity(id="project-624", display_name="Project 624", created_at=UTC_TIMESTAMP)

        returned = save_project_identity(path, identity)
        contents = path.read_text(encoding="utf-8")

        assert returned is None
        assert json.loads(contents) == identity.model_dump(mode="json")
        assert contents.startswith("{\n  ")
        assert contents.endswith("\n")
        assert not (tmp_path / "project.json.tmp").exists()

    def test_save_project_identity_lock_timeout_propagates_and_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Raise the LockTimeoutError("lock held") unchanged and leave project.json absent."""
        path = tmp_path / "project.json"
        identity = ProjectIdentity(id="project-624", display_name="Project 624", created_at=UTC_TIMESTAMP)
        monkeypatch.setattr(WorkspaceLock, "__enter__", raise_lock_timeout)

        with pytest.raises(LockTimeoutError, match=r"^lock held$"):
            save_project_identity(path, identity)

        assert not path.exists()

    def test_save_project_identity_write_oserror_raises_write_failed_from_cause(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "project.json"
        identity = ProjectIdentity(id="project-624", display_name="Project 624", created_at=UTC_TIMESTAMP)
        _fail_atomic_write(monkeypatch)

        with pytest.raises(ProjectIdentityError) as raised:
            save_project_identity(path, identity)

        assert raised.value.error_code == ErrorCode.PROJECT_IDENTITY_WRITE_FAILED
        assert raised.value.message == (f"Unable to write project identity at '{path}': disk full.")
        assert type(raised.value.__cause__) is OSError
        assert raised.value.remediations == ()
        assert not path.exists()

    @pytest.mark.parametrize("project_id", ["Bad Id!", "ab", "-leading-dash", "abc\n"])
    def test_validate_project_id_invalid_raises_project_id_invalid(self, project_id: str) -> None:
        with pytest.raises(ProjectIdentityError) as raised:
            validate_project_id(project_id)

        assert raised.value.error_code == ErrorCode.PROJECT_ID_INVALID
        assert raised.value.message == "Invalid project ID: must match ^[a-z0-9][a-z0-9-_]{2,62}$"
        assert raised.value.remediations == (
            Remediation(message=f"Pass a valid '--id' matching '{PROJECT_ID_REGEX}'."),
            Remediation(message="Run 'dovo init' without '--id' to generate a project ID.", command="dovo init"),
        )

    def test_validate_project_id_valid_returns_none(self) -> None:
        assert validate_project_id("custom-id") is None

    @pytest.mark.parametrize(
        ("project_id", "force"),
        [(None, False), ("custom-id", False), (None, True)],
        ids=["generated", "explicit_id", "force_without_id"],
    )
    def test_provision_project_identity_missing_file_creates(
        self, tmp_path: Path, project_id: str | None, force: bool
    ) -> None:
        provisioned = provision_project_identity(tmp_path, project_id=project_id, force=force)

        path = tmp_path / "project.json"
        assert provisioned.outcome == ProjectIdentityOutcome.CREATED
        assert provisioned.warning is None
        assert provisioned.path == path
        assert provisioned.identity == ProjectIdentity.model_validate_json(path.read_text(encoding="utf-8"))
        assert re.match(PROJECT_ID_REGEX, provisioned.identity.id) is not None
        if project_id is not None:
            assert provisioned.identity.id == project_id

    @freeze_time(UTC_TIMESTAMP)
    @pytest.mark.parametrize("existing", ["valid", "corrupt"])
    def test_provision_project_identity_with_id_and_force_overwrites(self, tmp_path: Path, existing: str) -> None:
        path = tmp_path / "project.json"
        if existing == "valid":
            _write_identity(path, ProjectIdentity(id="existing-id", display_name="Existing", created_at=UTC_TIMESTAMP))
        else:
            path.write_text("{not-json", encoding="utf-8")

        provisioned = provision_project_identity(tmp_path, project_id="new-id", force=True)

        assert provisioned.outcome == ProjectIdentityOutcome.OVERWRITTEN
        assert provisioned.warning is None
        assert provisioned.identity.id == "new-id"
        assert ProjectIdentity.model_validate_json(path.read_text(encoding="utf-8")).id == "new-id"

    @pytest.mark.parametrize(
        ("project_id", "force", "warning"),
        [
            pytest.param(
                None,
                False,
                "Project identity already exists (id=existing-id); preserving existing identity. "
                "Rerun with --id <new-id> --force to replace it.",
                id="rerun",
            ),
            pytest.param(
                "new-id",
                False,
                "Project identity already exists (id=existing-id); ignoring --id 'new-id' since --force "
                "was not passed. Rerun with --id new-id --force to replace it.",
                id="id_without_force",
            ),
            pytest.param(
                None,
                True,
                "Project identity already exists (id=existing-id); --force has no effect without --id, "
                "so the existing identity was preserved.",
                id="force_without_id",
            ),
        ],
    )
    def test_provision_project_identity_existing_preserves_with_warning(
        self, tmp_path: Path, project_id: str | None, force: bool, warning: str
    ) -> None:
        existing = ProjectIdentity(id="existing-id", display_name="Existing", created_at=UTC_TIMESTAMP)
        path = tmp_path / "project.json"
        _write_identity(path, existing)
        before = path.read_bytes()

        provisioned = provision_project_identity(tmp_path, project_id=project_id, force=force)

        assert provisioned.outcome == ProjectIdentityOutcome.PRESERVED
        assert provisioned.identity == existing
        assert provisioned.warning == warning
        assert path.read_bytes() == before

    @pytest.mark.parametrize("failure", ["corrupt_existing", "write_failed"])
    def test_provision_project_identity_propagates_load_and_save_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
    ) -> None:
        path = tmp_path / "project.json"
        if failure == "corrupt_existing":
            path.write_text("{not-json", encoding="utf-8")
            with pytest.raises(ValidationError) as validation:
                ProjectIdentity.model_validate_json("{not-json")
            expected_code = ErrorCode.PROJECT_IDENTITY_SCHEMA_INVALID
            expected_message = f"Project identity at '{path}' is invalid: {validation.value}."
        else:
            _fail_atomic_write(monkeypatch)
            expected_code = ErrorCode.PROJECT_IDENTITY_WRITE_FAILED
            expected_message = f"Unable to write project identity at '{path}': disk full."

        with pytest.raises(ProjectIdentityError) as raised:
            provision_project_identity(tmp_path)

        assert raised.value.error_code == expected_code
        assert raised.value.message == expected_message
        assert raised.value.remediations == ()
