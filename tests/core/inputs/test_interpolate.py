"""Contract tests for placeholder interpolation against execution metadata."""

from __future__ import annotations

from dovo.core.inputs.services.interpolate import interpolate_string
from dovo.engine.executors.models import ExecutionMetadata, PreviousStepMetadata, StepMetadata


def _metadata_with_step_a_outputs(outputs: dict[str, str]) -> ExecutionMetadata:
    """Build ExecutionMetadata with a single historical step_a entry carrying the given outputs."""
    return ExecutionMetadata(
        step=StepMetadata(id="step_b", index=2),
        steps=[
            PreviousStepMetadata(
                id="step_a",
                name="Step Alpha",
                index="1",
                status="completed",
                exit_code="0",
                outputs=outputs,
            )
        ],
    )


class InterpolateStepOutputsPlaceholderTests:
    """[tier-1/unit] interpolate_string: ${{ steps.<id>.outputs.<key> }} and bracket-quoted form resolution."""

    def test_dot_form_resolves_matching_step_id_and_output_key(self) -> None:
        """[tier-1/unit] interpolate_string: '${{ steps.step_a.outputs.greeting }}' against metadata.steps containing a step_a entry with outputs={'greeting': 'hello'} resolves to 'hello'."""
        metadata = _metadata_with_step_a_outputs({"greeting": "hello"})

        result = interpolate_string("${{ steps.step_a.outputs.greeting }}", metadata=metadata)

        assert result == "hello"

    def test_bracket_quoted_form_resolves_matching_step_id_and_output_key(self) -> None:
        """[tier-1/unit] interpolate_string: "${{ steps['step_a'].outputs.greeting }}" against the same metadata resolves to 'hello', identically to the dot form."""
        metadata = _metadata_with_step_a_outputs({"greeting": "hello"})

        result = interpolate_string("${{ steps['step_a'].outputs.greeting }}", metadata=metadata)

        assert result == "hello"

    def test_unknown_step_id_resolves_to_empty_string(self) -> None:
        """[tier-1/unit] interpolate_string: '${{ steps.missing_step.outputs.greeting }}' against metadata with no matching step id resolves to ''."""
        metadata = _metadata_with_step_a_outputs({"greeting": "hello"})

        result = interpolate_string("${{ steps.missing_step.outputs.greeting }}", metadata=metadata)

        assert result == ""

    def test_unknown_output_key_resolves_to_empty_string(self) -> None:
        """[tier-1/unit] interpolate_string: '${{ steps.step_a.outputs.missing_key }}' against a step_a entry whose outputs dict lacks 'missing_key' resolves to ''."""
        metadata = _metadata_with_step_a_outputs({"greeting": "hello"})

        result = interpolate_string("${{ steps.step_a.outputs.missing_key }}", metadata=metadata)

        assert result == ""

    def test_non_outputs_steps_expression_still_resolves_via_existing_field_path(self) -> None:
        """[tier-1/unit] interpolate_string: '${{ steps[0].status }}' against metadata.steps still resolves to the entry's status field, unaffected by the new outputs-selector branch."""
        metadata = _metadata_with_step_a_outputs({"greeting": "hello"})

        result = interpolate_string("${{ steps[0].status }}", metadata=metadata)

        assert result == "completed"
