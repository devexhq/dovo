"""Interaction prompter that answers a pending ask from pre-validated headless input."""

from __future__ import annotations

from collections.abc import Mapping

from dovo.core.interaction import InteractionAnswer, InteractionRequest
from dovo.engine.models import InteractionPrompter


class PresetInteractionPrompter(InteractionPrompter):
    """Answers one pending ask from pre-validated headless input and delegates every other ask."""

    def __init__(self, answers: Mapping[str, InteractionAnswer], fallback: InteractionPrompter | None) -> None:
        """Bind the step-id keyed answers and the prompter that handles any later ask."""
        self._answers = dict(answers)
        self._fallback = fallback

    def ask(self, request: InteractionRequest) -> InteractionAnswer | None:
        """Return and consume the preset answer for request.step_id, else delegate to the fallback, else None."""
        preset = self._answers.pop(request.step_id, None)
        if preset is not None:
            return preset
        return self._fallback.ask(request) if self._fallback is not None else None
