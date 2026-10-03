"""Unit tests for dovo.core.project.services.identity."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from freezegun import freeze_time

from dovo.common.lock import LockTimeoutError
from dovo.core.project.models import (
    PROJECT_ID_REGEX,
    ProjectIdentity,
    ProjectIdentityErrorType,
    ProjectIdentityLoadStatus,
    ProjectIdentityProvisionStatus,
    ProjectIdentitySaveStatus,
)
from dovo.core.project.services import identity as identity_service
from dovo.core.project.services.identity import (
    generate_project_identity,
    load_project_identity,
    provision_project_identity,
    save_project_identity,
)

UTC_TIMESTAMP = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


class ProjectIdentityServiceTests:
    """Unit tests for project identity services."""

    @freeze_time(UTC_TIMESTAMP)
    def test_generate_project_identity_with_explicit_values_returns_utc_identity(self) -> None:
        """Explicit project values are retained with the frozen UTC creation timestamp."""
        identity = generate_project_identity("project-624", "Project 624")

        assert identity.id == "project-624"
        assert identity.display_name == "Project 624"
        assert identity.created_at == UTC_TIMESTAMP

    def test_generate_project_identity_without_id_uses_generated_slug(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An omitted project ID uses the generated slug collaborator and a UTC timestamp."""
        monkeypatch.setattr(identity_service, "generate_slug", lambda: "calm-otter")

        identity = generate_project_identity(display_name="Generated Project")

        assert identity.id == "calm-otter"
        assert identity.created_at.utcoffset() == UTC_TIMESTAMP.utcoffset()

    def test_load_project_identity_valid_json_returns_ok_result(self, tmp_path: Path) -> None:
        """Valid serialized JSON returns the complete persisted identity."""
        path = tmp_path / "project.json"
        identity = ProjectIdentity(id="project-624", display_name="Project 624", created_at=UTC_TIMESTAMP)
        path.write_text(identity.model_dump_json(), encoding="utf-8")

        result = load_project_identity(path)

        assert result.status == ProjectIdentityLoadStatus.OK
        assert result.path == path
        assert result.identity == identity
        assert result.error is None

    def test_load_project_identity_missing_path_returns_not_found_result(self, tmp_path: Path) -> None:
        """An absent project identity file returns NOT_FOUND with its requested path."""
        path = tmp_path / "project.json"

        result = load_project_identity(path)

        assert result.status == ProjectIdentityLoadStatus.NOT_FOUND
        assert result.path == path
        assert result.error is not None
        assert result.error.error_type == ProjectIdentityErrorType.NOT_FOUND
        assert result.error.path == str(path)
        assert result.errors == [result.error.message]

    @pytest.mark.parametrize(
        "contents",
        [
            pytest.param("{not-json", id="malformed_json"),
            pytest.param('{"id": "Bad", "created_at": "2026-01-02T03:04:05Z"}', id="invalid_identity"),
        ],
    )
    def test_load_project_identity_invalid_content_returns_invalid_schema_result(
        self, tmp_path: Path, contents: str
    ) -> None:
        """Malformed or schema-invalid JSON returns INVALID_SCHEMA with its requested path."""
        path = tmp_path / "project.json"
        path.write_text(contents, encoding="utf-8")

        result = load_project_identity(path)

        assert result.status == ProjectIdentityLoadStatus.INVALID_SCHEMA
        assert result.path == path
        assert result.error is not None
        assert result.error.error_type == ProjectIdentityErrorType.INVALID_SCHEMA
        assert result.error.path == str(path)
        assert result.errors == [result.error.message]

    def test_load_project_identity_unreadable_path_returns_unreadable_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A read OSError returns UNREADABLE with its requested path."""
        path = tmp_path / "project.json"
        path.write_text("{}", encoding="utf-8")

        def raise_read_error(_path: Path, *args: object, **kwargs: object) -> str:
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "read_text", raise_read_error)

        result = load_project_identity(path)

        assert result.status == ProjectIdentityLoadStatus.UNREADABLE
        assert result.path == path
        assert result.error is not None
        assert result.error.error_type == ProjectIdentityErrorType.UNREADABLE
        assert result.error.path == str(path)
        assert result.errors == [result.error.message]

    def test_load_project_identity_invalid_utf8_bytes_returns_unreadable_result(self, tmp_path: Path) -> None:
        """Invalid UTF-8 bytes return UNREADABLE with its requested path."""
        path = tmp_path / "project.json"
        path.write_bytes(b"\xff\xfe\x00invalid")

        result = load_project_identity(path)

        assert result.status == ProjectIdentityLoadStatus.UNREADABLE
        assert result.path == path
        assert result.error is not None
        assert result.error.error_type == ProjectIdentityErrorType.UNREADABLE
        assert result.error.path == str(path)
        assert result.errors == [result.error.message]

    def test_save_project_identity_writes_indented_json_without_temp_sibling(self, tmp_path: Path) -> None:
        """A valid identity is persisted as indented JSON without a temporary sibling."""
        path = tmp_path / "project.json"
        identity = ProjectIdentity(id="project-624", display_name="Project 624", created_at=UTC_TIMESTAMP)

        result = save_project_identity(path, identity)
        contents = path.read_text(encoding="utf-8")

        assert result.status == ProjectIdentitySaveStatus.OK
        assert result.path == path
        assert json.loads(contents) == identity.model_dump(mode="json")
        assert contents.startswith("{\n  ")
        assert contents.endswith("\n")
        assert not (tmp_path / "project.json.tmp").exists()

    def test_save_project_identity_atomic_write_oserror_returns_write_failed_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An atomic-write OSError returns WRITE_FAILED with its requested path."""
        path = tmp_path / "project.json"
        identity = ProjectIdentity(id="project-624", display_name="Project 624", created_at=UTC_TIMESTAMP)

        def raise_write_error(_path: Path, _data: dict[str, object]) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(identity_service.Filesystem, "atomic_write_json", raise_write_error)

        result = save_project_identity(path, identity)

        assert result.status == ProjectIdentitySaveStatus.WRITE_FAILED
        assert result.path == path
        assert result.error is not None
        assert result.error.error_type == ProjectIdentityErrorType.WRITE_FAILED
        assert result.error.path == str(path)
        assert "disk full" in result.error.message
        assert result.errors == [result.error.message]

    def test_save_project_identity_lock_timeout_returns_locked_result(self, tmp_path: Path) -> None:
        """A workspace lock timeout returns LOCKED with its requested path."""
        path = tmp_path / "project.json"
        identity = ProjectIdentity(id="project-624", display_name="Project 624", created_at=UTC_TIMESTAMP)

        with patch(
            "dovo.core.project.services.identity.WorkspaceLock.__enter__",
            side_effect=LockTimeoutError("lock held"),
        ):
            result = save_project_identity(path, identity)

        assert result.status == ProjectIdentitySaveStatus.LOCKED
        assert result.path == path
        assert result.error is not None
        assert result.error.error_type == ProjectIdentityErrorType.LOCKED
        assert result.error.path == str(path)
        assert "lock held" in result.error.message
        assert result.errors == [result.error.message]

    def test_provision_project_identity_without_id_generates_slug_and_creates(self, tmp_path: Path) -> None:
        """No --id writes project.json with a PROJECT_ID_REGEX-matching generated slug, status=CREATED."""
        result = provision_project_identity(tmp_path)

        assert result.status == ProjectIdentityProvisionStatus.CREATED
        assert result.identity is not None
        assert re.match(PROJECT_ID_REGEX, result.identity.id) is not None
        assert (tmp_path / "project.json").exists()

    def test_provision_project_identity_with_valid_id_creates_with_explicit_id(self, tmp_path: Path) -> None:
        """A valid explicit --id and display name are persisted with status=CREATED."""
        result = provision_project_identity(tmp_path, project_id="custom-id", display_name="Custom")

        assert result.status == ProjectIdentityProvisionStatus.CREATED
        assert result.identity is not None
        assert result.identity.id == "custom-id"
        assert result.identity.display_name == "Custom"

    def test_provision_project_identity_with_invalid_id_returns_invalid_id_status(self, tmp_path: Path) -> None:
        """A malformed --id returns status=INVALID_ID with the exact literal error and writes nothing."""
        result = provision_project_identity(tmp_path, project_id="Bad Id!")

        assert result.status == ProjectIdentityProvisionStatus.INVALID_ID
        assert result.errors == [f"Invalid project ID: must match {PROJECT_ID_REGEX}"]
        assert result.fixes == [f"Pass a valid --id matching {PROJECT_ID_REGEX}, or omit --id to generate one."]
        assert not (tmp_path / "project.json").exists()

    def test_provision_project_identity_existing_without_force_preserves_identity(self, tmp_path: Path) -> None:
        """A pre-existing identity with no id/force is preserved with a generic warning."""
        existing = ProjectIdentity(id="existing-id", display_name="Existing", created_at=UTC_TIMESTAMP)
        path = tmp_path / "project.json"
        path.write_text(existing.model_dump_json(), encoding="utf-8")
        before = path.read_text(encoding="utf-8")

        result = provision_project_identity(tmp_path)

        assert result.status == ProjectIdentityProvisionStatus.PRESERVED
        assert result.identity == existing
        assert result.warnings == [
            "Project identity already exists (id=existing-id); preserving existing identity. "
            "Rerun with --id <new-id> --force to replace it."
        ]
        assert path.read_text(encoding="utf-8") == before

    def test_provision_project_identity_existing_with_id_no_force_ignores_id_and_preserves(
        self, tmp_path: Path
    ) -> None:
        """A pre-existing identity with --id but no --force is preserved, naming the ignored id."""
        existing = ProjectIdentity(id="existing-id", display_name="Existing", created_at=UTC_TIMESTAMP)
        path = tmp_path / "project.json"
        path.write_text(existing.model_dump_json(), encoding="utf-8")

        result = provision_project_identity(tmp_path, project_id="new-id")

        assert result.status == ProjectIdentityProvisionStatus.PRESERVED
        assert result.identity is not None
        assert result.identity.id == "existing-id"
        assert result.warnings == [
            "Project identity already exists (id=existing-id); ignoring --id 'new-id' since --force "
            "was not passed. Rerun with --id new-id --force to replace it."
        ]

    def test_provision_project_identity_existing_with_force_no_id_preserves(self, tmp_path: Path) -> None:
        """A pre-existing identity with force=True but no id is preserved, warning that --force needs --id."""
        existing = ProjectIdentity(id="existing-id", display_name="Existing", created_at=UTC_TIMESTAMP)
        path = tmp_path / "project.json"
        path.write_text(existing.model_dump_json(), encoding="utf-8")

        result = provision_project_identity(tmp_path, force=True)

        assert result.status == ProjectIdentityProvisionStatus.PRESERVED
        assert result.identity is not None
        assert result.identity.id == "existing-id"
        assert result.warnings == [
            "Project identity already exists (id=existing-id); --force has no effect without --id, "
            "so the existing identity was preserved."
        ]

    @freeze_time(UTC_TIMESTAMP)
    def test_provision_project_identity_existing_with_id_and_force_overwrites_identity(self, tmp_path: Path) -> None:
        """A pre-existing identity with id and force=True is replaced and persisted to disk."""
        existing = ProjectIdentity(id="existing-id", display_name="Existing", created_at=UTC_TIMESTAMP)
        path = tmp_path / "project.json"
        path.write_text(existing.model_dump_json(), encoding="utf-8")

        result = provision_project_identity(tmp_path, project_id="new-id", force=True)

        assert result.status == ProjectIdentityProvisionStatus.OVERWRITTEN
        assert result.identity is not None
        assert result.identity.id == "new-id"
        persisted = ProjectIdentity.model_validate_json(path.read_text(encoding="utf-8"))
        assert persisted.id == "new-id"

    def test_provision_project_identity_existing_corrupt_without_force_returns_failed_status(
        self, tmp_path: Path
    ) -> None:
        """An unreadable existing project.json without id+force returns FAILED, file left untouched."""
        path = tmp_path / "project.json"
        path.write_text("{not-json", encoding="utf-8")

        result = provision_project_identity(tmp_path)

        assert result.status == ProjectIdentityProvisionStatus.FAILED
        assert result.errors
        assert path.read_text(encoding="utf-8") == "{not-json"

    def test_provision_project_identity_existing_corrupt_with_id_and_force_recovers(self, tmp_path: Path) -> None:
        """An unreadable existing project.json with id+force returns OVERWRITTEN, replacing the corrupt file."""
        path = tmp_path / "project.json"
        path.write_text("{not-json", encoding="utf-8")

        result = provision_project_identity(tmp_path, project_id="new-id", force=True)

        assert result.status == ProjectIdentityProvisionStatus.OVERWRITTEN
        assert result.identity is not None
        assert result.identity.id == "new-id"
        persisted = ProjectIdentity.model_validate_json(path.read_text(encoding="utf-8"))
        assert persisted.id == "new-id"
