"""Contract tests for placeholder interpolation against execution metadata."""

from __future__ import annotations

import pytest

from dovo.core.catalog.definitions import StepDefinition, StepType
from dovo.core.inputs.services.interpolate import (
    interpolate_step_fields,
    interpolate_string,
    referenced_artifact_fields,
)
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


class InterpolateArtifactsTests:
    """[tier-1/unit] interpolate_string / interpolate_step_fields: the artifacts.<name>.<field> namespace."""

    @pytest.mark.parametrize("template", ["{{ artifacts.plan.revision }}", "${{ artifacts.plan.revision }}"])
    def test_artifact_revision_resolves_in_both_syntaxes(self, template: str) -> None:
        """[tier-1/unit] interpolate_string: artifacts={'plan': {'revision': '2'}} renders both syntaxes as '2'."""
        assert interpolate_string(template, artifacts={"plan": {"revision": "2"}}) == "2"

    @pytest.mark.parametrize("template", ["{{ artifacts.ghost.digest }}", "{{ artifacts.plan.bogus }}"])
    def test_unknown_artifact_or_field_renders_empty_string(self, template: str) -> None:
        """[tier-1/unit] interpolate_string: unknown artifact name or unknown field renders ''; an unknown steps.* placeholder stays literal."""
        metadata = _metadata_with_step_a_outputs({})

        rendered = interpolate_string(
            f"{template}|{{{{ steps.nope }}}}", metadata=metadata, artifacts={"plan": {"revision": "2"}}
        )

        assert rendered == "|{{ steps.nope }}"

    def test_dotted_artifact_name_uses_last_segment_as_field(self) -> None:
        """[tier-1/unit] interpolate_string: 'artifacts.team.plan.digest' resolves name 'team.plan' and field 'digest'."""
        rendered = interpolate_string("{{ artifacts.team.plan.digest }}", artifacts={"team.plan": {"digest": "abc"}})

        assert rendered == "abc"

    @pytest.mark.parametrize(
        ("step_kwargs", "field_name", "expected"),
        [
            pytest.param(
                {"type": StepType.COMMAND, "command": "cat {{ artifacts.plan.dir }}"},
                "command",
                "cat /a/plan/2",
                id="command",
            ),
            pytest.param(
                {"type": StepType.AGENT, "prompt": "read {{ artifacts.plan.dir }}"},
                "prompt",
                "read /a/plan/2",
                id="prompt",
            ),
            pytest.param(
                {"type": StepType.SCRIPT, "script_path": "{{ artifacts.plan.dir }}/run.sh"},
                "script_path",
                "/a/plan/2/run.sh",
                id="script_path",
            ),
            pytest.param({"run": "ls {{ artifacts.plan.dir }}"}, "run", "ls /a/plan/2", id="run"),
        ],
    )
    def test_artifacts_resolve_in_interpolated_step_fields(
        self, step_kwargs: dict[str, object], field_name: str, expected: str
    ) -> None:
        """[tier-1/unit] interpolate_step_fields: {{ artifacts.plan.dir }} renders in command, prompt, script_path and run."""
        step = StepDefinition.model_validate({"id": "s1", **step_kwargs})

        rendered = interpolate_step_fields(step, artifacts={"plan": {"dir": "/a/plan/2"}})

        assert getattr(rendered, field_name) == expected

    def test_artifacts_resolve_in_env_values(self) -> None:
        """[tier-1/unit] interpolate_step_fields: {{ artifacts.plan.dir }} renders inside an env value."""
        step = StepDefinition(
            id="s1", type=StepType.COMMAND, command="true", env={"PLAN_DIR": "{{ artifacts.plan.dir }}"}
        )

        rendered = interpolate_step_fields(step, artifacts={"plan": {"dir": "/a/plan/2"}})

        assert rendered.env == {"PLAN_DIR": "/a/plan/2"}

    def test_referenced_artifact_fields_collects_names_and_fields_from_all_fields(self) -> None:
        """[tier-1/unit] referenced_artifact_fields: prompt content, env revision and command dir placeholders -> {'plan': {'content', 'revision'}, 'b': {'dir'}}; unknown fields are ignored."""
        step = StepDefinition(
            id="s1",
            type=StepType.AGENT,
            prompt="{{ artifacts.plan.content }} {{ artifacts.plan.bogus }}",
            command="cat {{ artifacts.b.dir }}",
            env={"REV": "${{ artifacts.plan.revision }}"},
        )

        assert referenced_artifact_fields(step) == {"plan": {"content", "revision"}, "b": {"dir"}}
