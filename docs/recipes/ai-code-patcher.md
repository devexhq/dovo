# Recipe: Agent Steps and Quality Gates

This recipe demonstrates a blueprint that runs agent steps for planning, patching, and review alongside explicit quality-gate commands. Each agent step sends its prompt to the configured provider inside the worktree; see [Agent-Step Adapters](../guides/agent-providers.md).

---

## The Blueprint

Create `.dovo/catalog/blueprints/ai-feature-dev.yml`:

```yaml
name: ai-feature-dev
description: Plan, patch, and review with agent steps and run quality gates
summary: Agent steps with explicit verification commands
version: 1
use_worktree: true

inputs:
  issue_description:
    type: string
    description: Description of the feature or bug to implement
    required: true
    aliases: ["-d", "--desc"]

steps:
  # 1. Sync branch state
  - id: git-sync
    uses: dovo/git-sync-base

  # 2. Plan the change
  - id: ai-planner
    name: Plan the implementation
    type: agent
    prompt: "Plan the implementation for: ${{ inputs.issue_description }}. Report the plan and leave the files unchanged."
    timeout_seconds: 180

  # 3. Implement the change
  - id: ai-patcher
    name: Implement the change
    type: agent
    prompt: "Implement: ${{ inputs.issue_description }}"
    timeout_seconds: 300

  # 4. Verification & Quality Gates
  - id: run-linters
    name: Check code formatting and types
    run: ruff check . && basedpyright src
    assert:
      exit_code: 0
    on_failure:
      action: retry
      max_retries: 2
      backoff_ms: 1000
      on_max_retries: prompt_user

  - id: run-tests
    name: Execute test suite
    run: pytest
    assert:
      exit_code: 0

  # 5. Review the change
  - id: ai-reviewer
    name: Review the change
    type: agent
    prompt: "Review the changes in this worktree for: ${{ inputs.issue_description }}. Report findings and leave the files unchanged."
    timeout_seconds: 180
```

---

## Running the Recipe

Execute the blueprint with a feature description:

```bash
dovo run ai-feature-dev --desc "Add support for custom HTTP timeouts in the API client"
```

If a command step fails, you can interactively choose to retry, or later resume with `dovo resume blueprint_<id>`.
