"""Agent adapter interfaces and provider implementations."""

from dovo.core.agents.base import BaseAgentProvider, ProviderSpec
from dovo.core.agents.cli_mutation import (
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_PATCH_KB,
    DEFAULT_REJECT_BINARY_CHANGES,
    CliDirectMutationAdapter,
    CliMutationOutcome,
    CliMutationRunFn,
    CliMutationRunRequest,
    build_mutation_prompt,
    validate_request_patch,
)
from dovo.core.agents.copilot import CopilotAgentAdapter
from dovo.core.agents.factory import get_agent_adapter
from dovo.core.agents.models import (
    AgentAttempt,
    AgentFailurePayload,
    AgentInvocationContext,
    AgentRequest,
    AgentResponse,
    AgentResponseStatus,
    AgentScratchResult,
    PayloadFile,
    PayloadOmission,
    ResolvedAgentSettings,
)
from dovo.core.agents.registry import PROVIDERS
from dovo.core.agents.scratch import allocate_invocation_paths, new_invocation_id
from dovo.core.agents.services.run_direct import run_direct_attempt

__all__ = [
    "DEFAULT_MAX_FILES",
    "DEFAULT_MAX_PATCH_KB",
    "DEFAULT_REJECT_BINARY_CHANGES",
    "PROVIDERS",
    "AgentAttempt",
    "AgentFailurePayload",
    "AgentInvocationContext",
    "AgentRequest",
    "AgentResponse",
    "AgentResponseStatus",
    "AgentScratchResult",
    "BaseAgentProvider",
    "CliDirectMutationAdapter",
    "CliMutationOutcome",
    "CliMutationRunFn",
    "CliMutationRunRequest",
    "CopilotAgentAdapter",
    "PayloadFile",
    "PayloadOmission",
    "ProviderSpec",
    "ResolvedAgentSettings",
    "allocate_invocation_paths",
    "build_mutation_prompt",
    "get_agent_adapter",
    "new_invocation_id",
    "run_direct_attempt",
    "validate_request_patch",
]
