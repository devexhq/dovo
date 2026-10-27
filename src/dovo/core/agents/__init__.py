"""Agent adapter interfaces and provider implementations."""

from dovo.core.agents.base import BaseAgentProvider, ProviderSpec
from dovo.core.agents.claude import ClaudeAgentAdapter
from dovo.core.agents.cli_mutation import (
    CliDirectMutationAdapter,
    CliMutationOutcome,
    CliMutationRunFn,
    CliMutationRunRequest,
    build_mutation_prompt,
    validate_request_patch,
)
from dovo.core.agents.copilot import CopilotAgentAdapter
from dovo.core.agents.exceptions import AgentError
from dovo.core.agents.factory import get_agent_adapter
from dovo.core.agents.models import (
    AgentAttempt,
    AgentAttemptContext,
    AgentEnvMode,
    AgentEnvOverrides,
    AgentFailurePayload,
    AgentInvocationContext,
    AgentProgress,
    AgentProgressKind,
    AgentRequest,
    AgentResponse,
    AgentResponseStatus,
    EnvPassthroughEntry,
    PayloadFile,
    PayloadOmission,
    ResolvedAgentSettings,
)
from dovo.core.agents.registry import PROVIDERS
from dovo.core.agents.scratch import allocate_invocation_paths, new_invocation_id
from dovo.core.agents.services.run_direct import run_direct_attempt
from dovo.core.agents.tools import (
    PolicyRoots,
    check_policy_support,
    default_tool_policy,
    resolve_policy_roots,
    resolve_tool_policy,
    tool_policy_unsupported_message,
)

__all__ = [
    "PROVIDERS",
    "AgentAttempt",
    "AgentAttemptContext",
    "AgentEnvMode",
    "AgentEnvOverrides",
    "AgentError",
    "AgentFailurePayload",
    "AgentInvocationContext",
    "AgentProgress",
    "AgentProgressKind",
    "AgentRequest",
    "AgentResponse",
    "AgentResponseStatus",
    "BaseAgentProvider",
    "ClaudeAgentAdapter",
    "CliDirectMutationAdapter",
    "CliMutationOutcome",
    "CliMutationRunFn",
    "CliMutationRunRequest",
    "CopilotAgentAdapter",
    "EnvPassthroughEntry",
    "PayloadFile",
    "PayloadOmission",
    "PolicyRoots",
    "ProviderSpec",
    "ResolvedAgentSettings",
    "allocate_invocation_paths",
    "build_mutation_prompt",
    "check_policy_support",
    "default_tool_policy",
    "get_agent_adapter",
    "new_invocation_id",
    "resolve_policy_roots",
    "resolve_tool_policy",
    "run_direct_attempt",
    "tool_policy_unsupported_message",
    "validate_request_patch",
]
