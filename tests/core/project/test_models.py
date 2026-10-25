"""Unit tests for dovo.core.project.models."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from dovo.core.project.models import ProjectIdentity

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
        with pytest.raises(ValidationError):
            ProjectIdentity(id="project-623", display_name=display_name, created_at=created_at)

    def test_unknown_field_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            ProjectIdentity.model_validate(
                {
                    "id": "project-623",
                    "created_at": UTC_TIMESTAMP,
                    "unknown_field": "disallowed",
                }
            )
