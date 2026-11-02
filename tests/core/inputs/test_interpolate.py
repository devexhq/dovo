"""Contract tests for placeholder interpolation against execution metadata."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path as FilePath

import pytest
import yaml

from dovo.core.catalog.definitions import StepDefinition, StepType
from dovo.core.expressions import Path
from dovo.core.inputs import (
    UNDEFINED,
    ExecutionMetadataView,
    NamespaceProvider,
    NamespaceResolutionError,
    build_providers,
    interpolate_step_fields,
    interpolate_string,
)
from dovo.engine.executors.models import ExecutionMetadata, PreviousStepMetadata, StepMetadata


class _MappingProvider(NamespaceProvider):
    """NamespaceProvider answering exact paths from a fixed mapping."""

    def __init__(self, namespace: str, values: dict[str, str]) -> None:
        self.namespace = namespace
        self._values = values

    def resolve(self, path: Path) -> object:
        return self._values.get(path.text, UNDEFINED)


class _RaisingProvider(NamespaceProvider):
    """NamespaceProvider that always fails with a user-facing message."""

    namespace = "boom"

    def resolve(self, path: Path) -> object:
        raise NamespaceResolutionError("boom")


_BARE = object()
_GRADE_PR = FilePath(__file__).resolve().parents[3] / ".dovo" / "catalog" / "blueprints" / "grade-pr.yml"


class _BareMetadata(ExecutionMetadataView):
    """ExecutionMetadataView whose namespaces carry no fields."""

    @property
    def step(self) -> object:
        return _BARE

    @property
    def blueprint(self) -> object:
        return _BARE

    @property
    def previous_step(self) -> object:
        return _BARE

    @property
    def iteration(self) -> object:
        return _BARE

    @property
    def steps(self) -> Sequence[object]:
        return ()


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
    def test_dot_form_resolves_matching_step_id_and_output_key(self) -> None:
        metadata = _metadata_with_step_a_outputs({"greeting": "hello"})

        result = interpolate_string("${{ steps.step_a.outputs.greeting }}", build_providers(metadata=metadata))

        assert result == "hello"

    def test_bracket_quoted_form_resolves_matching_step_id_and_output_key(self) -> None:
        metadata = _metadata_with_step_a_outputs({"greeting": "hello"})

        result = interpolate_string("${{ steps['step_a'].outputs.greeting }}", build_providers(metadata=metadata))

        assert result == "hello"

    def test_unknown_step_id_resolves_to_empty_string(self) -> None:
        metadata = _metadata_with_step_a_outputs({"greeting": "hello"})

        result = interpolate_string("${{ steps.missing_step.outputs.greeting }}", build_providers(metadata=metadata))

        assert result == ""

    def test_unknown_output_key_resolves_to_empty_string(self) -> None:
        metadata = _metadata_with_step_a_outputs({"greeting": "hello"})

        result = interpolate_string("${{ steps.step_a.outputs.missing_key }}", build_providers(metadata=metadata))

        assert result == ""

    def test_non_outputs_steps_expression_still_resolves_via_existing_field_path(self) -> None:
        metadata = _metadata_with_step_a_outputs({"greeting": "hello"})

        result = interpolate_string("${{ steps[0].status }}", build_providers(metadata=metadata))

        assert result == "completed"

    @pytest.mark.parametrize(
        ("outputs", "placeholder", "rendered"),
        [
            pytest.param({}, "{{ steps.code.outputs.status }}", "no_op", id="status"),
            pytest.param({}, "{{ steps.code.outputs.touched_files }}", '["a.py", "b.py"]', id="touched_files"),
            pytest.param({}, "{{ steps.code.outputs.summary }}", "", id="null_summary"),
            pytest.param({"status": "done"}, "{{ steps.code.outputs.status }}", "done", id="recorded_wins"),
        ],
    )
    def test_stdout_json_key_renders_as_recorded_outputs_do(
        self, outputs: dict[str, str], placeholder: str, rendered: str
    ) -> None:
        stdout = '{"status":"no_op","summary":null,"unfixable_reason":null,"touched_files":["a.py","b.py"]}\n'
        code = PreviousStepMetadata(
            id="code", index="1", status="completed", exit_code="0", outputs=outputs, stdout=stdout
        )
        metadata = ExecutionMetadata(step=StepMetadata(id="report", index=2), steps=[code])

        assert interpolate_string(placeholder, build_providers(metadata=metadata)) == rendered


def _finished_steps_metadata() -> ExecutionMetadata:
    """Build metadata with finished steps `a` (completed) and `b` (failed), current step `c` and previous step `b`."""
    a = PreviousStepMetadata(id="a", name="Alpha", index="1", status="completed", exit_code="0")
    b = PreviousStepMetadata(id="b", index="2", status="failed", exit_code="1")
    return ExecutionMetadata(step=StepMetadata(id="c", index=3), previous_step=b, steps=[a, b])


def _metadata_with_step_s(*, outputs: dict[str, str] | None = None, stdout: str = "") -> ExecutionMetadata:
    """Build metadata with one finished step `s` carrying the given outputs and stdout."""
    s = PreviousStepMetadata(id="s", index="1", status="completed", exit_code="0", outputs=outputs or {}, stdout=stdout)
    return ExecutionMetadata(step=StepMetadata(id="t", index=2), steps=[s])


class InterpolateStepsPlaceholderTests:
    @pytest.mark.parametrize(
        ("template", "rendered"),
        [
            pytest.param("{{ steps[0].id }}", "a", id="first_by_position"),
            pytest.param("{{ steps[-1].status }}", "failed", id="last_by_position"),
            pytest.param("{{ steps['b'].exit_code }}", "1", id="quoted_id"),
            pytest.param("{{ steps.a.name }}", "Alpha", id="dot_form_id"),
            pytest.param("{{ steps[5].id }}", "", id="out_of_range"),
            pytest.param("{{ steps.zzz.status }}", "", id="unknown_id"),
            pytest.param("{{ steps.a.bogus }}", "{{ steps.a.bogus }}", id="unknown_field"),
            pytest.param("{{ steps.a }}", "{{ steps.a }}", id="step_without_field"),
            pytest.param("{{ steps.0.id }}", "", id="numeric_dotted_segment_is_an_id"),
            pytest.param("{{ previous_step.exit_code }}", "1", id="previous_step"),
            pytest.param("{{ step.index }}", "3", id="current_step"),
            pytest.param("{{ iteration.index }}", "1", id="iteration"),
        ],
    )
    def test_existing_step_form_keeps_its_result(self, template: str, rendered: str) -> None:
        providers = build_providers(metadata=_finished_steps_metadata())

        assert interpolate_string(template, providers) == rendered

    @pytest.mark.parametrize(
        ("template", "rendered"),
        [
            pytest.param(
                "{{ steps['a.b'].status }}|{{ steps.a.b.status }}", "completed|{{ steps.a.b.status }}", id="step_id"
            ),
            pytest.param("{{ inputs['a.b'] }}|{{ a.b }}", "v|{{ a.b }}", id="input_name"),
        ],
    )
    def test_dotted_name_reads_only_through_brackets(self, template: str, rendered: str) -> None:
        step = PreviousStepMetadata(id="a.b", index="1", status="completed", exit_code="0")
        metadata = ExecutionMetadata(step=StepMetadata(id="c", index=2), steps=[step])

        assert interpolate_string(template, build_providers(inputs={"a.b": "v"}, metadata=metadata)) == rendered

    @pytest.mark.parametrize(
        ("template", "rendered"),
        [
            pytest.param("{{ steps.s.outputs.a.b }}", "x", id="dotted_path"),
            pytest.param("{{ steps.s.outputs['a.b'] }}", "whole", id="bracket_key"),
            pytest.param("{{ steps['s'].outputs['a.b'] }}", "whole", id="bracket_step_and_key"),
            pytest.param("{{ steps[0].outputs.t }}", "plain", id="position_then_output"),
            pytest.param("{{ steps.s.outputs.a.c }}", "", id="missing_path"),
            pytest.param("{{ steps.s.outputs.t.u }}", "", id="not_json"),
            pytest.param("{{ steps.s.outputs }}", "{{ steps.s.outputs }}", id="outputs_without_key"),
        ],
    )
    def test_output_path_reads_json_or_a_bracketed_key(self, template: str, rendered: str) -> None:
        outputs = {"a": '{"b": "x"}', "a.b": "whole", "t": "plain"}

        assert (
            interpolate_string(template, build_providers(metadata=_metadata_with_step_s(outputs=outputs))) == rendered
        )


class InterpolateRenderingTests:
    @pytest.mark.parametrize(
        ("template", "rendered"),
        [
            pytest.param("{{ inputs.s }}", "text", id="string"),
            pytest.param("${{ inputs.flag }}", "true", id="true_input"),
            pytest.param("{{ off }}", "false", id="false_input"),
            pytest.param("{{ inputs.n }}", "3", id="integer_input"),
            pytest.param("{{ steps.s.outputs.n }}", "2.5", id="number_from_stdout"),
            pytest.param("{{ steps.s.outputs.files }}", '["a.py", "b.py"]', id="list_from_stdout"),
            pytest.param("{{ steps.s.outputs.o }}", '{"k": 1}', id="object_from_stdout"),
            pytest.param("{{ steps.s.outputs.z }}", "", id="null_from_stdout"),
            pytest.param("{{ steps.s.outputs.missing }}", "", id="absent_output"),
        ],
    )
    def test_value_renders_by_its_type(self, template: str, rendered: str) -> None:
        metadata = _metadata_with_step_s(stdout='{"n": 2.5, "files": ["a.py", "b.py"], "o": {"k": 1}, "z": null}')
        inputs: dict[str, object] = {"s": "text", "flag": True, "off": False, "n": 3}

        assert interpolate_string(template, build_providers(inputs=inputs, metadata=metadata)) == rendered

    @pytest.mark.parametrize(
        "template",
        [
            pytest.param("{{ github.sha }}", id="unknown_root"),
            pytest.param("{{ other }}", id="unknown_input"),
            pytest.param("{{ 'x' }}", id="quoted_literal"),
            pytest.param("{{ 5 }}", id="number"),
            pytest.param("{{ .Values.image }}", id="word_from_another_template"),
            pytest.param("{{ two words }}", id="unquoted_text"),
            pytest.param("{{ steps[ 0 ].id }}", id="spaced_index"),
            pytest.param("{{ steps[a].id }}", id="unquoted_bracket_id"),
        ],
    )
    def test_body_that_no_namespace_defines_stays_as_text(self, template: str) -> None:
        providers = build_providers(inputs={"s": "text"}, metadata=_finished_steps_metadata())

        assert interpolate_string(template, providers) == template

    def test_grade_pr_post_step_compares_the_rendered_boolean(self) -> None:
        blueprint = yaml.safe_load(_GRADE_PR.read_text(encoding="utf-8"))
        post = StepDefinition.model_validate(next(step for step in blueprint["steps"] if step["id"] == "post"))

        rendered = interpolate_step_fields(post, build_providers(inputs={"pr": 7, "post": False}))

        assert rendered.env is not None
        assert rendered.env["POST"] == "false"
        assert rendered.run is not None
        assert '"$POST" = "false"' in rendered.run


class InterpolateProvidersTests:
    def test_first_provider_defining_a_path_wins(self) -> None:
        providers = [_MappingProvider("x", {"x.y": "a"}), _MappingProvider("x", {"x.y": "b"})]

        assert interpolate_string("{{ x.y }}", providers) == "a"

    def test_placeholder_without_owning_provider_stays_literal(self) -> None:
        providers = build_providers(metadata=_metadata_with_step_a_outputs({}))

        assert interpolate_string("{{ nope.field }}|{{ steps.nope }}", providers) == "{{ nope.field }}|{{ steps.nope }}"

    def test_provider_exception_propagates_from_interpolate_string(self) -> None:
        with pytest.raises(NamespaceResolutionError, match="boom"):
            interpolate_string("{{ boom.x }}", [_RaisingProvider()])

    @pytest.mark.parametrize(
        "template",
        [pytest.param("{{ inputs.branch }}", id="namespaced"), pytest.param("{{ branch }}", id="bare")],
    )
    def test_inputs_provider_resolves_namespaced_and_bare_names(self, template: str) -> None:
        assert interpolate_string(template, build_providers(inputs={"branch": "main"})) == "main"

    @pytest.mark.parametrize(
        "template",
        [pytest.param("{{ inputs.other }}", id="namespaced"), pytest.param("{{ other }}", id="bare")],
    )
    def test_inputs_provider_leaves_unknown_names_literal(self, template: str) -> None:
        assert interpolate_string(template, build_providers(inputs={"branch": "main"})) == template

    def test_metadata_attribute_missing_from_namespace_stays_literal(self) -> None:
        providers = build_providers(metadata=_BareMetadata())

        assert interpolate_string("{{ step.id }}", providers) == "{{ step.id }}"

    def test_build_providers_without_inputs_or_metadata_is_empty(self) -> None:
        providers = build_providers()

        assert providers == []
        assert interpolate_string("{{ branch }}", providers) == "{{ branch }}"

    def test_build_providers_orders_metadata_artifacts_then_inputs(self) -> None:
        providers = build_providers(
            inputs={"branch": "main"},
            metadata=_metadata_with_step_a_outputs({}),
            artifacts=_MappingProvider("artifacts", {}),
        )

        assert [provider.namespace for provider in providers] == [
            "step",
            "blueprint",
            "previous_step",
            "iteration",
            "steps",
            "artifacts",
            "inputs",
        ]


class InterpolateArtifactsTests:
    @pytest.mark.parametrize("template", ["{{ artifacts.plan.revision }}", "${{ artifacts.plan.revision }}"])
    def test_artifact_revision_resolves_in_both_syntaxes(self, template: str) -> None:
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
        step = StepDefinition.model_validate({"id": "s1", **step_kwargs})
        providers = [_MappingProvider("artifacts", {"artifacts.plan.dir": "/a/plan/2"})]

        rendered = interpolate_step_fields(step, providers)

        assert getattr(rendered, field_name) == expected

    def test_artifacts_resolve_in_env_values(self) -> None:
        step = StepDefinition(
            id="s1", type=StepType.COMMAND, command="true", env={"PLAN_DIR": "{{ artifacts.plan.dir }}"}
        )
        providers = [_MappingProvider("artifacts", {"artifacts.plan.dir": "/a/plan/2"})]

        rendered = interpolate_step_fields(step, providers)

        assert rendered.env == {"PLAN_DIR": "/a/plan/2"}
