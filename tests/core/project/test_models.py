"""Unit tests for dovo.core.project.models."""

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from dovo.core.project.models import (
    ProjectIdentity,
    ProjectIdentityError,
    ProjectIdentityErrorType,
    ProjectIdentityLoadResult,
    ProjectIdentityLoadStatus,
    ProjectIdentitySaveResult,
    ProjectIdentitySaveStatus,
)

UTC_TIMESTAMP = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
NAIVE_TIMESTAMP = datetime(2026, 1, 2, 3, 4, 5)
NON_UTC_TIMESTAMP = datetime(2026, 1, 2, 4, 4, 5, tzinfo=timezone(timedelta(hours=1)))


class ProjectIdentityModelsTests:
    """Unit tests for ProjectIdentity validation."""

    @pytest.mark.parametrize(
        ("project_id", "display_name"),
        [
            pytest.param("abc", None, id="minimum_length"),
            pytest.param("a" + "b" * 62, "Dovo CLI", id="maximum_length"),
        ],
    )
    def test_valid_slug_boundaries_create_strict_identity(self, project_id: str, display_name: str | None) -> None:
        """ProjectIdentity retains valid boundary identifiers and UTC values."""
        identity = ProjectIdentity(
            id=project_id,
            display_name=display_name,
            created_at=UTC_TIMESTAMP,
        )

        assert identity.id == project_id
        assert identity.display_name == display_name
        assert identity.created_at == UTC_TIMESTAMP

    @pytest.mark.parametrize(
        "project_id",
        [
            pytest.param("ab", id="too_short"),
            pytest.param("a" + "b" * 63, id="too_long"),
            pytest.param("Abc", id="uppercase"),
            pytest.param("ab c", id="space"),
        ],
    )
    def test_malformed_slugs_raise_validation_error(self, project_id: str) -> None:
        """ProjectIdentity rejects malformed length, uppercase, and spaced IDs."""
        with pytest.raises(ValidationError):
            ProjectIdentity(id=project_id, created_at=UTC_TIMESTAMP)

    @pytest.mark.parametrize(
        ("display_name", "created_at"),
        [
            pytest.param("   ", UTC_TIMESTAMP, id="whitespace_display_name"),
            pytest.param(None, NAIVE_TIMESTAMP, id="naive_timestamp"),
            pytest.param(None, NON_UTC_TIMESTAMP, id="non_utc_timestamp"),
        ],
    )
    def test_blank_display_name_or_non_utc_timestamp_raises_validation_error(
        self, display_name: str | None, created_at: datetime
    ) -> None:
        """ProjectIdentity rejects blank display names and non-UTC timestamps."""
        with pytest.raises(ValidationError):
            ProjectIdentity(id="project-623", display_name=display_name, created_at=created_at)

    def test_unknown_field_raises_validation_error(self) -> None:
        """ProjectIdentity rejects unknown fields under strict validation."""
        with pytest.raises(ValidationError):
            ProjectIdentity.model_validate(
                {
                    "id": "project-623",
                    "created_at": UTC_TIMESTAMP,
                    "unknown_field": "disallowed",
                }
            )

    @pytest.mark.parametrize(
        ("dto_type", "payload"),
        [
            pytest.param(
                ProjectIdentityError,
                {
                    "error_type": ProjectIdentityErrorType.NOT_FOUND,
                    "message": "not found",
                    "path": "project.json",
                },
                id="error",
            ),
            pytest.param(
                ProjectIdentityLoadResult,
                {
                    "status": ProjectIdentityLoadStatus.OK,
                    "path": Path("project.json"),
                },
                id="load_result",
            ),
            pytest.param(
                ProjectIdentitySaveResult,
                {
                    "status": ProjectIdentitySaveStatus.OK,
                    "path": Path("project.json"),
                },
                id="save_result",
            ),
        ],
    )
    def test_persistence_dtos_reject_unknown_fields(
        self, dto_type: type[BaseModel], payload: dict[str, object]
    ) -> None:
        """Persistence DTOs reject fields outside their strict schemas."""
        with pytest.raises(ValidationError):
            dto_type.model_validate({**payload, "unknown_field": "disallowed"})

    @pytest.mark.parametrize(
        "result",
        [
            pytest.param(
                ProjectIdentityLoadResult(status=ProjectIdentityLoadStatus.OK, path=Path("project.json")),
                id="load_result",
            ),
            pytest.param(
                ProjectIdentitySaveResult(status=ProjectIdentitySaveStatus.OK, path=Path("project.json")),
                id="save_result",
            ),
        ],
    )
    def test_persistence_results_inherit_empty_base_result_envelope(
        self, result: ProjectIdentityLoadResult | ProjectIdentitySaveResult
    ) -> None:
        """Persistence result DTOs retain BaseResult's empty success envelope."""
        assert (result.errors, result.warnings, result.fixes, result.error_code) == ([], [], [], None)
        assert result.ok
