"""Test harness primitives, shared builders, and contract assertions."""

from tests.harness.builders import (
    AgentRequestBuilder,
    BlueprintBuilder,
    StepBuilder,
    WorkspaceBuilder,
)
from tests.harness.diffs import AGENT_ADAPTER_FACTORY, new_file_diff
from tests.harness.fakes import FakeAgentProvider, FakeAgentRunner, FakeAgentRunnerCall
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
    render_rich,
)

__all__ = [
    "AGENT_ADAPTER_FACTORY",
    "AgentRequestBuilder",
    "BlueprintBuilder",
    "FakeAgentProvider",
    "FakeAgentRunner",
    "FakeAgentRunnerCall",
    "FormatterCase",
    "StepBuilder",
    "WorkspaceBuilder",
    "assert_json_payload_matches_published_shape",
    "assert_rich_render_shows_every_view_value",
    "assert_transform_derives_expected_view",
    "new_file_diff",
    "render_rich",
]
