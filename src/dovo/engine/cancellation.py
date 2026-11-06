"""How an interrupt ends a run: stop every process group it started, then cancel it, or fail it when a group survives."""

from __future__ import annotations

from pydantic import BaseModel

from dovo.common.error_codes import ErrorCode
from dovo.common.models import Remediation
from dovo.common.process import process_registry
from dovo.core.db import SessionStatus
from dovo.engine.state_models import NodeState

CANCELLED_MESSAGE = "Execution cancelled by user."


class InterruptStop(BaseModel):
    """How an interrupt ends a run once its processes are stopped: cancelled, or failed when a process group survived."""

    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    status: SessionStatus
    node_state: NodeState
    error_code: ErrorCode
    errors: list[str]
    remediations: list[Remediation]


def stop_interrupted_run() -> InterruptStop:
    """Stop every process group the run started and return how the interrupt ends the run.

    With every group gone, the run and its started nodes end CANCELLED with RUN_CANCELLED. When a group survives,
    they end FAILED with SESSION_PROCESS_STOP_FAILED, one error and one `kill -9` remediation per surviving group.
    """
    survivors = process_registry.stop_all(grace_seconds=0.5)
    if not survivors:
        return InterruptStop(
            status=SessionStatus.CANCELLED,
            node_state=NodeState.CANCELLED,
            error_code=ErrorCode.RUN_CANCELLED,
            errors=[CANCELLED_MESSAGE],
            remediations=[],
        )
    return InterruptStop(
        status=SessionStatus.FAILED,
        node_state=NodeState.FAILED,
        error_code=ErrorCode.SESSION_PROCESS_STOP_FAILED,
        errors=[f"Run interrupted, but process group {pgid} is still running." for pgid in survivors],
        remediations=[
            Remediation(
                message=f"Its processes may still be running; stop them with 'kill -9 -{pgid}'",
                command=f"kill -9 -{pgid}",
            )
            for pgid in survivors
        ],
    )
