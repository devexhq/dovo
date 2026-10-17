"""Test harness primitives, shared builders, and contract assertions."""

from tests.harness.builders import (
    AgentRequestBuilder,
    BlueprintBuilder,
    StepBuilder,
    WorkspaceBuilder,
    init_test_database,
)
from tests.harness.diffs import AGENT_ADAPTER_FACTORY, new_file_diff
from tests.harness.fakes import (
    PROVIDER_FINISHED_STREAMS,
    EditingAgentRunner,
    FakeAgentProvider,
    FakeAgentRunner,
    FakeAgentRunnerCall,
    RecordingProgressSink,
    StreamingAgentRunner,
    patch_claude_probe,
    real_invocation,
)
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
    render_rich,
)
from tests.harness.workspace_paths import initialized_workspace_paths

__all__ = [
    "AGENT_ADAPTER_FACTORY",
    "PROVIDER_FINISHED_STREAMS",
    "AgentRequestBuilder",
    "BlueprintBuilder",
    "EditingAgentRunner",
    "FakeAgentProvider",
    "FakeAgentRunner",
    "FakeAgentRunnerCall",
    "FormatterCase",
    "RecordingProgressSink",
    "StepBuilder",
    "StreamingAgentRunner",
    "WorkspaceBuilder",
    "assert_json_payload_matches_published_shape",
    "assert_rich_render_shows_every_view_value",
    "assert_transform_derives_expected_view",
    "init_test_database",
    "initialized_workspace_paths",
    "new_file_diff",
    "patch_claude_probe",
    "real_invocation",
    "render_rich",
]
