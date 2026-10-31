"""Invariant: no file in src/dovo, tests or e2e uses explicit `Any` more often than its budget allows."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

from tests.lint.astlib import E2E_ROOT, REPO_ROOT, SRC_ROOT, TESTS_ROOT, collect_python_files

# Explicit `Any` uses per file that predate this rule. Counts are only ever lowered, never raised;
# a file that is not listed has a budget of zero.
ANY_BUDGET: Final[dict[str, int]] = {
    "src/dovo/cli/ui/dispatcher.py": 8,
    "src/dovo/cli/ui/formatters/config/config_views.py": 2,
    "src/dovo/cli/ui/formatters/doctor/doctor_views.py": 1,
    "src/dovo/cli/ui/formatters/registry.py": 1,
    "src/dovo/common/filesystem/filesystem.py": 1,
    "src/dovo/common/filesystem/models.py": 1,
    "src/dovo/common/filesystem/services/operations.py": 1,
    "src/dovo/common/process.py": 8,
    "src/dovo/common/schema_validation.py": 1,
    "src/dovo/core/agents/copilot.py": 6,
    "src/dovo/core/catalog/blueprint.py": 1,
    "src/dovo/core/catalog/catalog.py": 1,
    "src/dovo/core/catalog/definitions/blueprint.py": 4,
    "src/dovo/core/catalog/definitions/step.py": 1,
    "src/dovo/core/catalog/services/resolve_step.py": 2,
    "src/dovo/core/catalog/services/validate.py": 2,
    "src/dovo/core/config/generator.py": 5,
    "src/dovo/core/config/loader.py": 4,
    "src/dovo/core/config/models.py": 3,
    "src/dovo/core/config/mutate.py": 11,
    "src/dovo/core/config/serialize.py": 1,
    "src/dovo/core/config/tiers.py": 7,
    "src/dovo/core/config/validate.py": 1,
    "src/dovo/core/db/alembic/env.py": 1,
    "src/dovo/core/diagnostics/models.py": 1,
    "src/dovo/core/diagnostics/services/remediation.py": 1,
    "src/dovo/core/git/runner.py": 3,
    "src/dovo/core/inputs/interpolate.py": 1,
    "src/dovo/core/status/models.py": 1,
    "src/dovo/engine/executors/assertions/json_match.py": 1,
    "src/dovo/engine/executors/models.py": 2,
    "src/dovo/engine/writer.py": 1,
    "tests/cli/config/test_config_show.py": 1,
    "tests/cli/init/test_init.py": 2,
    "tests/cli/run/test_run.py": 2,
    "tests/cli/status/test_status.py": 2,
    "tests/cli/test_cli.py": 2,
    "tests/cli/ui/formatters/artifacts/test_artifacts_download.py": 1,
    "tests/cli/ui/formatters/artifacts/test_artifacts_list.py": 1,
    "tests/cli/ui/formatters/artifacts/test_artifacts_prune.py": 1,
    "tests/cli/ui/formatters/catalog/test_catalog_create.py": 1,
    "tests/cli/ui/formatters/catalog/test_catalog_delete.py": 1,
    "tests/cli/ui/formatters/catalog/test_catalog_list.py": 1,
    "tests/cli/ui/formatters/catalog/test_catalog_show.py": 1,
    "tests/cli/ui/formatters/catalog/test_catalog_validate.py": 1,
    "tests/cli/ui/formatters/config/test_config_load.py": 1,
    "tests/cli/ui/formatters/config/test_config_set.py": 1,
    "tests/cli/ui/formatters/config/test_config_unset.py": 1,
    "tests/cli/ui/formatters/config/test_config_validate.py": 1,
    "tests/cli/ui/formatters/events/test_agent_progress.py": 1,
    "tests/cli/ui/formatters/events/test_error_panel.py": 1,
    "tests/cli/ui/formatters/events/test_lock_wait.py": 1,
    "tests/cli/ui/formatters/events/test_loop.py": 1,
    "tests/cli/ui/formatters/events/test_message.py": 1,
    "tests/cli/ui/formatters/events/test_prompt.py": 1,
    "tests/cli/ui/formatters/events/test_run_success.py": 1,
    "tests/cli/ui/formatters/events/test_step_done.py": 1,
    "tests/cli/ui/formatters/events/test_step_output.py": 1,
    "tests/cli/ui/formatters/events/test_step_start.py": 1,
    "tests/cli/ui/formatters/events/test_warning.py": 1,
    "tests/cli/ui/formatters/events/test_worktree.py": 1,
    "tests/cli/ui/formatters/global_cli/test_banner.py": 1,
    "tests/cli/ui/formatters/init/test_workspace_init.py": 1,
    "tests/cli/ui/formatters/session/test_session_diff.py": 1,
    "tests/cli/ui/formatters/session/test_session_list.py": 1,
    "tests/cli/ui/formatters/session/test_session_logs.py": 2,
    "tests/cli/ui/formatters/session/test_session_show.py": 1,
    "tests/cli/ui/formatters/status/test_dovo_status.py": 1,
    "tests/cli/ui/formatters/worktree/test_pruned_item.py": 1,
    "tests/cli/ui/formatters/worktree/test_worktree_apply.py": 1,
    "tests/cli/ui/formatters/worktree/test_worktree_create.py": 1,
    "tests/cli/ui/formatters/worktree/test_worktree_delete.py": 1,
    "tests/cli/ui/formatters/worktree/test_worktree_diff.py": 1,
    "tests/cli/ui/formatters/worktree/test_worktree_list.py": 1,
    "tests/cli/ui/formatters/worktree/test_worktree_prune.py": 1,
    "tests/cli/ui/formatters/worktree/test_worktree_show.py": 1,
    "tests/cli/worktree/test_worktree_prune.py": 1,
    "tests/common/test_schema_validation.py": 1,
    "tests/common/test_tool_policy.py": 2,
    "tests/conftest.py": 2,
    "tests/core/config/test_config.py": 1,
    "tests/core/config/test_loader.py": 1,
    "tests/core/config/test_mutate.py": 4,
    "tests/core/config/test_tiers.py": 1,
    "tests/core/diagnostics/test_diagnostics.py": 1,
    "tests/engine/executors/assertions/test_json_match.py": 3,
    "tests/harness/formatter.py": 9,
    "tests/lint/test_config_schema_parity.py": 1,
}


def _is_any(node: ast.AST) -> bool:
    """Return whether a node names `Any`, bare or as `typing.Any`."""
    if isinstance(node, ast.Name):
        return node.id == "Any"
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "Any"
        and isinstance(node.value, ast.Name)
        and node.value.id == "typing"
    )


def _any_count(file_path: Path) -> int:
    """Return how many times one module names `Any`."""
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    return sum(1 for node in ast.walk(tree) if _is_any(node))


def _any_counts() -> dict[str, int]:
    """Return the `Any` count of every checked module that names it at least once."""
    files = [*collect_python_files(SRC_ROOT), *collect_python_files(TESTS_ROOT), *collect_python_files(E2E_ROOT)]
    assert files, "no Python files found, so the rule would pass vacuously"
    counts = {str(path.relative_to(REPO_ROOT)): _any_count(path) for path in files}
    return {path: count for path, count in counts.items() if count}


class ExplicitAnyTests:
    def test_no_file_exceeds_its_any_budget(self) -> None:
        over_budget = {path: count for path, count in _any_counts().items() if count > ANY_BUDGET.get(path, 0)}

        assert over_budget == {}, "replace the new `Any` with a real type or `object`"

    def test_any_budget_matches_current_counts(self) -> None:
        """Require each budget to equal its file's count, so a removed `Any` lowers the budget for good."""
        counts = _any_counts()
        stale = {path: counts.get(path, 0) for path, budget in ANY_BUDGET.items() if counts.get(path, 0) < budget}

        assert stale == {}, "lower these ANY_BUDGET entries to the counts shown (delete an entry at zero)"
