"""Unit tests for the packaged project identity schema."""

import socket

import pytest

from dovo.common.schema_validation import PROJECT_VALIDATOR


class ProjectSchemaTests:
    """Unit tests for PROJECT_VALIDATOR."""

    def test_project_validator_accepts_complete_utc_identity_without_network(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PROJECT_VALIDATOR accepts a complete UTC identity without socket creation."""
        monkeypatch.delattr(socket, "create_connection")

        result = PROJECT_VALIDATOR.validate(
            {
                "id": "project-623",
                "display_name": "Dovo CLI",
                "created_at": "2026-01-02T03:04:05Z",
            }
        )

        assert result.errors == []

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({"created_at": "2026-01-02T03:04:05Z"}, id="missing_id"),
            pytest.param({"id": "project-623"}, id="missing_created_at"),
            pytest.param(
                {"id": "Project-623", "created_at": "2026-01-02T03:04:05Z"},
                id="uppercase_id",
            ),
            pytest.param(
                {"id": "ab", "created_at": "2026-01-02T03:04:05Z"},
                id="short_id",
            ),
            pytest.param(
                {
                    "id": "project-623",
                    "display_name": "   ",
                    "created_at": "2026-01-02T03:04:05Z",
                },
                id="blank_display_name",
            ),
            pytest.param(
                {"id": "project-623", "created_at": "2026-01-02T03:04:05+01:00"},
                id="non_utc_timestamp",
            ),
            pytest.param(
                {"id": "project-623", "created_at": "not-a-timestamp"},
                id="malformed_timestamp",
            ),
            pytest.param(
                {
                    "id": "project-623",
                    "created_at": "2026-01-02T03:04:05Z",
                    "unknown_field": True,
                },
                id="extra_property",
            ),
        ],
    )
    def test_project_validator_rejects_invalid_payloads(self, payload: dict[str, object]) -> None:
        """PROJECT_VALIDATOR rejects missing, malformed, and extra properties."""
        result = PROJECT_VALIDATOR.validate(payload)

        assert result.errors
