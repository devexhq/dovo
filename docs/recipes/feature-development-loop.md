# Recipe: Feature Development Loop

A blueprint that plans a feature, then repeats a code step and a review step until the review has nothing left to fix. Agent steps run in the worktree, so the loop needs a Git worktree. See [Agent Providers](../guides/agent-providers.md).

---

## The Blueprint

Create `.dovo/catalog/blueprints/feature.yml`:

```yaml
name: feature
description: Plan a feature, then code and review it until the review finds nothing to fix
summary: Plan, code and review loop
version: 1
use_worktree: true

inputs:
  feature:
    type: string
    description: Description of the feature to build
    required: true
    aliases: ["-f", "--feature"]

steps:
  - id: plan
    name: Plan the feature
    type: agent
    prompt: "Plan the implementation of: ${{ inputs.feature }}. Write the plan to PLAN.md in the repository root and change nothing else."
    tools:
      allow:
        - capability: read
        - capability: write
    timeout_seconds: 180

  - id: build-loop
    name: Code and review
    type: loop
    max_iterations: 3
    until:
      - steps.review.outputs.status == "no_op"
    on_max_iterations: prompt_user
    do:
      - id: code
        name: Implement the plan
        type: agent
        prompt: "Implement the plan in PLAN.md for: ${{ inputs.feature }}. If the code already covers part of the plan, finish the rest."
        tools:
          allow:
            - capability: read
            - capability: write
            - capability: shell
        timeout_seconds: 300

      - id: review
        name: Review and fix
        type: agent
        prompt: "Review the changes against PLAN.md. Fix any bugs, gaps or failing tests you find. If nothing needs fixing, change nothing."
        tools:
          allow:
            - capability: read
            - capability: write
            - capability: shell
        timeout_seconds: 240

  - id: cleanup
    name: Remove the plan file
    run: rm -f PLAN.md
```

---

## Running the Recipe

```bash
dovo run feature --feature "Add a --json flag to the export command"
```

### What happens

1. `plan` writes a plan to `PLAN.md` in the worktree.
2. `code` implements the plan.
3. `review` checks the result and fixes problems it finds. If it changes files, its status is `proposed_patch` and the loop runs another iteration.
4. When `review` changes nothing, its status is `no_op` (`steps.review.outputs.status == "no_op"`) and the loop ends.
5. `cleanup` deletes `PLAN.md` so it is not part of the diff.

If the loop reaches `max_iterations` first, Dovo prompts you to continue or abort. Review the result with `dovo diff`, then apply it with `dovo worktree apply`.
