"""Contract tests for placeholder interpolation against execution metadata."""

from __future__ import annotations

import pytest

from dovo.core.catalog.definitions import StepDefinition, StepType
from dovo.core.inputs.exceptions import NamespaceResolutionError
from dovo.core.inputs.services.interpolate import (
    build_providers,
    interpolate_step_fields,
    interpolate_string,
    metadata_providers,
)
from dovo.engine.executors.models import ExecutionMetadata, PreviousStepMetadata, StepMetadata


class _MappingProvider:
    """NamespaceProvider answering exact paths from a fixed mapping."""

    def __init__(self, namespace: str, values: dict[str, str]) -> None:
        self.namespace = namespace
        self._values = values

    def resolve(self, path: str) -> str | None:
        return self._values.get(path)


class _RaisingProvider:
    """NamespaceProvider that always fails with a user-facing message."""

    namespace = "boom"

    def resolve(self, path: str) -> str | None:
        raise NamespaceResolutionError("boom")


class _BareMetadata:
    """ExecutionMetadataView whose namespaces carry no fields."""

    step = object()
    blueprint = object()
    previous_step = object()
    iteration = object()
    steps: tuple[object, ...] = ()


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

        result = interpolate_string("${{ steps.step_a.outputs.greeting }}", metadata_providers(metadata))

        assert result == "hello"

    def test_bracket_quoted_form_resolves_matching_step_id_and_output_key(self) -> None:
        """[tier-1/unit] interpolate_string: "${{ steps['step_a'].outputs.greeting }}" against the same metadata resolves to 'hello', identically to the dot form."""
        metadata = _metadata_with_step_a_outputs({"greeting": "hello"})

        result = interpolate_string("${{ steps['step_a'].outputs.greeting }}", metadata_providers(metadata))

        assert result == "hello"

    def test_unknown_step_id_resolves_to_empty_string(self) -> None:
        """[tier-1/unit] interpolate_string: '${{ steps.missing_step.outputs.greeting }}' against metadata with no matching step id resolves to ''."""
        metadata = _metadata_with_step_a_outputs({"greeting": "hello"})

        result = interpolate_string("${{ steps.missing_step.outputs.greeting }}", metadata_providers(metadata))

        assert result == ""

    def test_unknown_output_key_resolves_to_empty_string(self) -> None:
        """[tier-1/unit] interpolate_string: '${{ steps.step_a.outputs.missing_key }}' against a step_a entry whose outputs dict lacks 'missing_key' resolves to ''."""
        metadata = _metadata_with_step_a_outputs({"greeting": "hello"})

        result = interpolate_string("${{ steps.step_a.outputs.missing_key }}", metadata_providers(metadata))

        assert result == ""

    def test_non_outputs_steps_expression_still_resolves_via_existing_field_path(self) -> None:
        """[tier-1/unit] interpolate_string: '${{ steps[0].status }}' against metadata.steps still resolves to the entry's status field, unaffected by the new outputs-selector branch."""
        metadata = _metadata_with_step_a_outputs({"greeting": "hello"})

        result = interpolate_string("${{ steps[0].status }}", metadata_providers(metadata))

        assert result == "completed"


class InterpolateProvidersTests:
    """[tier-1/unit] interpolate_string / build_providers: provider sequence resolution."""

    def test_first_provider_defining_a_path_wins(self) -> None:
        """[tier-1/unit] interpolate_string: providers [A, B] both defining 'x.y' ('a', 'b') -> '{{ x.y }}' renders 'a'."""
        providers = [_MappingProvider("x", {"x.y": "a"}), _MappingProvider("x", {"x.y": "b"})]

        assert interpolate_string("{{ x.y }}", providers) == "a"

    def test_placeholder_without_owning_provider_stays_literal(self) -> None:
        """[tier-1/unit] interpolate_string: '{{ nope.field }}' with only metadata providers -> returned unchanged."""
        providers = metadata_providers(_metadata_with_step_a_outputs({}))

        assert interpolate_string("{{ nope.field }}|{{ steps.nope }}", providers) == "{{ nope.field }}|{{ steps.nope }}"

    def test_provider_exception_propagates_from_interpolate_string(self) -> None:
        """[tier-1/unit] interpolate_string: a provider raising NamespaceResolutionError('boom') -> the same exception escapes to the caller."""
        with pytest.raises(NamespaceResolutionError, match="boom"):
            interpolate_string("{{ boom.x }}", [_RaisingProvider()])

    @pytest.mark.parametrize(
        "template",
        [pytest.param("{{ inputs.branch }}", id="namespaced"), pytest.param("{{ branch }}", id="bare")],
    )
    def test_inputs_provider_resolves_namespaced_and_bare_names(self, template: str) -> None:
        """[tier-1/unit] build_providers: inputs={'branch': 'main'} renders both '{{ inputs.branch }}' and '{{ branch }}' as 'main'."""
        assert interpolate_string(template, build_providers(inputs={"branch": "main"})) == "main"

    @pytest.mark.parametrize(
        "template",
        [pytest.param("{{ inputs.other }}", id="namespaced"), pytest.param("{{ other }}", id="bare")],
    )
    def test_inputs_provider_leaves_unknown_names_literal(self, template: str) -> None:
        """[tier-1/unit] build_providers: inputs={'branch': 'main'} leaves '{{ inputs.other }}' and '{{ other }}' unchanged."""
        assert interpolate_string(template, build_providers(inputs={"branch": "main"})) == template

    def test_metadata_attribute_missing_from_namespace_stays_literal(self) -> None:
        """[tier-1/unit] metadata_providers: metadata whose step object has no 'id' attribute leaves '{{ step.id }}' unchanged."""
        providers = metadata_providers(_BareMetadata())

        assert interpolate_string("{{ step.id }}", providers) == "{{ step.id }}"

    def test_build_providers_without_inputs_or_metadata_is_empty(self) -> None:
        """[tier-1/unit] build_providers: no inputs, metadata or artifacts -> []; interpolate_string returns the template unchanged."""
        providers = build_providers()

        assert providers == []
        assert interpolate_string("{{ branch }}", providers) == "{{ branch }}"

    def test_build_providers_orders_metadata_artifacts_then_inputs(self) -> None:
        """[tier-1/unit] build_providers: metadata, artifacts provider and inputs supplied -> namespaces in order step..iteration, steps, artifacts, inputs."""
        providers = build_providers(
            inputs={"branch": "main"},
            metadata=_metadata_with_step_a_outputs({}),
            artifacts=_MappingProvider("artifacts", {}),
        )

        assert [provider.namespace for provider in providers] == [
            "step",
            "blueprint",
            "task",
            "workflow",
            "previous_step",
            "iteration",
            "steps",
            "artifacts",
            "inputs",
        ]


class InterpolateArtifactsTests:
    """[tier-1/unit] interpolate_string / interpolate_step_fields: a provider-backed artifacts.<name>.<field> namespace."""

    @pytest.mark.parametrize("template", ["{{ artifacts.plan.revision }}", "${{ artifacts.plan.revision }}"])
    def test_artifact_revision_resolves_in_both_syntaxes(self, template: str) -> None:
        """[tier-1/unit] interpolate_string: an artifacts provider answering revision '2' renders both syntaxes as '2'."""
        providers = [_MappingProvider("artifacts", {"artifacts.plan.revision": "2"})]

        assert interpolate_string(template, providers) == "2"

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
        providers = [_MappingProvider("artifacts", {"artifacts.plan.dir": "/a/plan/2"})]

        rendered = interpolate_step_fields(step, providers)

        assert getattr(rendered, field_name) == expected

    def test_artifacts_resolve_in_env_values(self) -> None:
        """[tier-1/unit] interpolate_step_fields: {{ artifacts.plan.dir }} renders inside an env value."""
        step = StepDefinition(
            id="s1", type=StepType.COMMAND, command="true", env={"PLAN_DIR": "{{ artifacts.plan.dir }}"}
        )
        providers = [_MappingProvider("artifacts", {"artifacts.plan.dir": "/a/plan/2"})]

        rendered = interpolate_step_fields(step, providers)

        assert rendered.env == {"PLAN_DIR": "/a/plan/2"}
