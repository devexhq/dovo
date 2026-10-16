"""Contract tests for mapping Copilot stdout events to agent progress items."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dovo.core.agents import AgentProgress, AgentProgressKind
from dovo.core.agents.copilot_progress import COPILOT_TARGET_FIELDS, COPILOT_TOOL_KINDS, map_copilot_line
from tests.harness.fixtures import load_agent_fixture

_WORKTREE = Path("/work/repo")
_FIXTURE_NAMES = [
    "claude_planning",
    "claude_implementing_create",
    "claude_implementing_edit",
    "claude_reviewing",
    "gpt_planning_patch",
    "gpt_implementing_glob",
    "gpt_unmapped_skill",
]

_EXPECTED_SEQUENCES: dict[str, list[tuple[AgentProgressKind, str]]] = {
    "claude_planning": [
        (AgentProgressKind.SHELL, "head -c 600 pyproject.toml; ls .agentic 2>&1 | head"),
        (AgentProgressKind.SEARCH, "smoke-feature-dev"),
        (AgentProgressKind.SHELL, "mkdir -p .agentic"),
        (AgentProgressKind.EDIT, ".agentic/plan.md"),
    ],
    "claude_implementing_create": [
        (AgentProgressKind.READ, ".agentic/plan.md"),
        (AgentProgressKind.SHELL, "ls .agentic"),
        (AgentProgressKind.EDIT, ".agentic/smoke/a.txt"),
        (AgentProgressKind.EDIT, ".agentic/smoke/b.txt"),
        (AgentProgressKind.SHELL, "mkdir -p .agentic/smoke"),
        (AgentProgressKind.EDIT, ".agentic/smoke/a.txt"),
        (AgentProgressKind.EDIT, ".agentic/smoke/b.txt"),
    ],
    "claude_implementing_edit": [
        (AgentProgressKind.SHELL, "cat .agentic/review.json; ls .agentic/smoke; head -c 200 .agentic/smoke/*"),
        (AgentProgressKind.EDIT, "/tmp/unused"),
        (AgentProgressKind.EDIT, ".agentic/smoke/b.txt"),
        (AgentProgressKind.SHELL, "rm /tmp/unused; cat -A .agentic/smoke/b.txt"),
    ],
    "claude_reviewing": [
        (AgentProgressKind.SHELL, "cat .agents/skills/smoke-review/SKILL.md; ls .agentic 2>&1 | head"),
        (AgentProgressKind.READ, ".agentic/smoke/a.txt"),
        (AgentProgressKind.READ, ".agentic/smoke/b.txt"),
        (AgentProgressKind.READ, ".agentic/plan.md"),
        (AgentProgressKind.SEARCH, "value=fixed"),
        (
            AgentProgressKind.SHELL,
            "cd .agentic && printf 'Approved\\n' > review.md && printf '{\"findings\": []}\\n' > review.json && printf 'APPROVE' > verdict.txt && cat review.md review.json verdict.txt",
        ),
    ],
    "gpt_planning_patch": [
        (AgentProgressKind.READ, "pyproject.toml"),
        (AgentProgressKind.SEARCH, "smoke-feature-dev"),
        (AgentProgressKind.EDIT, ".agentic/plan.md"),
    ],
    "gpt_implementing_glob": [
        (AgentProgressKind.READ, ".agentic/review.json"),
        (AgentProgressKind.READ, "b.txt"),
        (AgentProgressKind.SEARCH, "**/b.txt"),
        (AgentProgressKind.EDIT, "b.txt"),
        (
            AgentProgressKind.SHELL,
            "printf '%s\\n' '--- content ---'; cat b.txt; printf '%s\\n' '--- status ---'; git status --short; printf '%s\\n' '--- staged ---'; git diff --cached --name-only; printf '%s\\n' '--- unstaged names ---'; git diff --name-only",
        ),
    ],
    "gpt_unmapped_skill": [
        (AgentProgressKind.READ, "/work/home/.agents/skills/surgical-patch/agents/openai.yaml"),
        (AgentProgressKind.READ, ".agentic/review.json"),
        (
            AgentProgressKind.SHELL,
            "git status --short && printf '\\n--- withheld ---\\n' && printf '%s\\n' \"$DOVO_ENV_WITHHELD\"",
        ),
        (AgentProgressKind.READ, "b.txt"),
        (
            AgentProgressKind.SHELL,
            'test "$(cat b.txt)" = \'value=fixed;\' && test "$(wc -l < b.txt)" -eq 1 && git status --short',
        ),
    ],
}


def _tool_line(tool_name: str, arguments: object) -> str:
    return json.dumps({"type": "tool.execution_start", "data": {"toolName": tool_name, "arguments": arguments}})


def _map(line: str) -> tuple[AgentProgress, ...]:
    return map_copilot_line(line, worktree_path=_WORKTREE)


class MapCopilotLineTests:
    @pytest.mark.parametrize(
        ("tool_name", "arguments", "kind", "target"),
        [
            pytest.param(
                "view", {"path": "/work/repo/src/x.py"}, AgentProgressKind.READ, "src/x.py", id="view-relative"
            ),
            pytest.param(
                "grep", {"pattern": "needle", "paths": "src"}, AgentProgressKind.SEARCH, "needle", id="grep-pattern"
            ),
            pytest.param("bash", {"command": "ls\n  -la"}, AgentProgressKind.SHELL, "ls -la", id="bash-collapsed"),
            pytest.param(
                "view",
                {"path": "/elsewhere/x.py"},
                AgentProgressKind.READ,
                "/elsewhere/x.py",
                id="outside-worktree-absolute",
            ),
        ],
    )
    def test_mapped_tool_returns_kind_and_target(
        self, tool_name: str, arguments: dict[str, str], kind: AgentProgressKind, target: str
    ) -> None:
        assert _map(_tool_line(tool_name, arguments)) == (AgentProgress(kind=kind, target=target),)

    def test_apply_patch_returns_one_item_per_file_in_order(self) -> None:
        patch = "*** Begin Patch\n*** Add File: a.txt\n+x\n*** Update File: b.txt\n@@\n-y\n+z\n*** End Patch\n"

        assert _map(_tool_line("apply_patch", patch)) == (
            AgentProgress(kind=AgentProgressKind.EDIT, target="a.txt"),
            AgentProgress(kind=AgentProgressKind.EDIT, target="b.txt"),
        )

    @pytest.mark.parametrize(
        "line",
        [
            pytest.param("not json", id="not-json"),
            pytest.param('"a string"', id="json-non-object"),
            pytest.param('{"type":"assistant.message","data":{}}', id="unmapped-event-type"),
            pytest.param(
                '{"type":"tool.execution_start","data":{"toolName":"skill","arguments":{"skill":"x"}}}',
                id="unmapped-tool",
            ),
            pytest.param(
                '{"type":"tool.execution_start","data":{"toolName":"view","arguments":{}}}', id="missing-target-field"
            ),
            pytest.param(
                '{"type":"tool.execution_start","data":{"toolName":"apply_patch","arguments":{}}}',
                id="patch-wrong-type",
            ),
        ],
    )
    def test_skipped_line_returns_empty_tuple(self, line: str) -> None:
        assert _map(line) == ()

    @pytest.mark.parametrize("fixture_name", _FIXTURE_NAMES)
    def test_fixture_log_maps_to_its_expected_progress_sequence(self, fixture_name: str) -> None:
        mapped = [progress for line in load_agent_fixture("copilot", fixture_name) for progress in _map(line)]

        assert [(progress.kind, progress.target) for progress in mapped] == _EXPECTED_SEQUENCES[fixture_name]


class MapCopilotFixtureCoverageTests:
    def test_fixtures_exercise_every_tool_in_the_mapping(self) -> None:
        tool_names: set[str] = set()
        kinds: set[AgentProgressKind] = set()
        for fixture_name in _FIXTURE_NAMES:
            for line in load_agent_fixture("copilot", fixture_name):
                event = json.loads(line)
                if event.get("type") == "tool.execution_start":
                    tool_names.add(event["data"]["toolName"])
                kinds.update(progress.kind for progress in _map(line))

        assert tool_names >= set(COPILOT_TOOL_KINDS)
        assert kinds == set(COPILOT_TOOL_KINDS.values())


class CopilotProgressContractTests:
    def test_every_tool_except_apply_patch_has_a_target_field(self) -> None:
        assert set(COPILOT_TOOL_KINDS) - {"apply_patch"} == set(COPILOT_TARGET_FIELDS)
