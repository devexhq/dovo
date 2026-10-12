"""Contract tests for PresetInteractionPrompter."""

from __future__ import annotations

from dovo.core.interaction import InteractionAnswer, InteractionRequest, RequestOption
from dovo.engine import PresetInteractionPrompter
from dovo.engine.models import InteractionPrompter


class _FixedPrompter(InteractionPrompter):
    """InteractionPrompter answering every request with a fixed selection."""

    def __init__(self, selected: str) -> None:
        self.selected = selected

    def ask(self, request: InteractionRequest) -> InteractionAnswer | None:
        return InteractionAnswer(selected=[self.selected])


def _request(step_id: str) -> InteractionRequest:
    """Return a confirm request for step_id."""
    return InteractionRequest(
        step_id=step_id,
        mode="confirm",
        message="Go?",
        options=[RequestOption(value="approve", label="Approve"), RequestOption(value="deny", label="Deny", fail=True)],
    )


class PresetInteractionPrompterTests:
    """Contract tests for PresetInteractionPrompter.ask."""

    def test_preset_answer_is_returned_once_then_the_fallback_is_used(self) -> None:
        """[tier-1/unit] PresetInteractionPrompter.ask: the first ask for the preset step id returns the preset answer; a second ask returns the fallback's answer."""
        prompter = PresetInteractionPrompter(
            {"approve": InteractionAnswer(selected=["approve"])}, _FixedPrompter("deny")
        )

        first = prompter.ask(_request("approve"))
        second = prompter.ask(_request("approve"))

        assert first == InteractionAnswer(selected=["approve"])
        assert second == InteractionAnswer(selected=["deny"])

    def test_without_fallback_an_unmatched_ask_returns_none(self) -> None:
        """[tier-1/unit] PresetInteractionPrompter.ask: a request whose step id has no preset and no fallback returns None."""
        prompter = PresetInteractionPrompter({"approve": InteractionAnswer(selected=["approve"])}, None)

        assert prompter.ask(_request("other")) is None
