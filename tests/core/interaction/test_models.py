"""Contract tests for interaction models persisted in session state."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dovo.core.interaction import InteractionRequest


class InteractionRequestContractTests:
    """InteractionRequest is persisted as `pending_ask`; its serialized shape must stay resumable."""

    def test_serialized_shape_and_defaults_are_pinned(self) -> None:
        """[tier-1/unit] InteractionRequest: a minimal text request serializes to exactly step_id, mode, message, empty options, the default disabled comment and a null artifact."""
        request = InteractionRequest(step_id="why", mode="text", message="Why?")

        assert request.model_dump(mode="json") == {
            "step_id": "why",
            "mode": "text",
            "message": "Why?",
            "options": [],
            "comment": {"policy": "disabled", "message": "Comment"},
            "artifact": None,
        }

    def test_stored_payload_round_trips_and_unknown_fields_are_rejected(self) -> None:
        """[tier-1/unit] InteractionRequest: a stored payload validates back to an equal request; an extra field raises ValidationError."""
        stored = {
            "step_id": "approve",
            "mode": "select",
            "message": "Go?",
            "options": [{"value": "a", "label": "A", "fail": False, "comment": "optional"}],
            "comment": {"policy": "optional", "message": "Why"},
            "artifact": {"name": "plan", "revision": 2, "digest": "abc", "preview": ["x"]},
        }

        request = InteractionRequest.model_validate(stored)

        assert request.model_dump(mode="json") == stored
        with pytest.raises(ValidationError):
            InteractionRequest.model_validate({**stored, "extra": 1})
