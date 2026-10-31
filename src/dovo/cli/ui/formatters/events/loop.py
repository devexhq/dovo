"""ComponentFormatter for LoopLifecycleEvent."""

from __future__ import annotations

from rich.console import Group
from rich.text import Text

from dovo.cli.ui.events import LoopLifecycleEvent
from dovo.cli.ui.formatters.base import ComponentFormatter


def _build_conditions_evaluated_text(data: LoopLifecycleEvent) -> Group:
    """Build the per-condition Rich Text lines for a conditions_evaluated event.

    Content is appended to Text via .append rather than interpolated into a
    markup string, so loop ids and condition expressions never need escaping.
    """
    label = data.name or data.loop_id
    lines = [Text(f"[{label}] Evaluated 'until' conditions:")]
    for condition in data.conditions:
        lines.append(Text(f"  - {condition.expression}: {condition.detail}"))
    if data.next_iteration is not None:
        lines.append(Text(f"[{label}] Conditions not met. Continuing to iteration {data.next_iteration}..."))
    return Group(*lines)


class LoopLifecycleFormatter(ComponentFormatter[LoopLifecycleEvent]):
    """Formatter for loop execution notices."""

    def to_rich(self, data: LoopLifecycleEvent) -> Text | Group:
        """Render loop progress, iteration, evaluation, or termination line, labelled with the loop's name or id."""
        label = data.name or data.loop_id
        if data.action == "start":
            return Text(f"[{label}] Starting loop block (max_iterations: {data.max_iterations})")
        if data.action == "iteration_start":
            return Text(f"[{label}] --- Iteration {data.iteration}/{data.max_iterations} ---")
        if data.action == "conditions_evaluated":
            return _build_conditions_evaluated_text(data)
        if data.action == "done":
            if data.status == "completed":
                return Text(f"[{label}] Loop completed successfully in {data.iteration} iteration(s).")
            return Text(f"[{label}] Loop terminated with status '{data.status}' after {data.iteration} iteration(s).")
        return Text(data.message or f"[{label}] {data.action}")
