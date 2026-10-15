# Dovo

Run AI agent and developer workflows as blueprints, each in its own isolated Git worktree.
Plan with an agent, approve it yourself, and review the diff before anything touches your branch.

```yaml
name: feature
description: Plan a feature until approved, then implement it
version: 1
use_worktree: true

inputs:
  feature:
    type: string
    description: Description of the feature to build
    required: true
    aliases: ["-f", "--feature"]

steps:
  - id: plan-loop
    name: Plan until approved
    type: loop
    max_iterations: 3
    until:
      - steps.approve.outputs.selected == "approve"
    on_max_iterations: prompt_user
    do:
      - id: plan
        type: agent
        prompt: |
          Plan the implementation of: ${{ inputs.feature }}. Write the plan to PLAN.md and change nothing else.
          Previous plan: {{ artifacts.plan.content }}
          Feedback to address: {{ steps.approve.outputs.comment }}
        tools:
          allow:
            - capability: read
            - capability: write
        artifacts:
          - name: plan
            path: PLAN.md
        timeout_seconds: 180

      - id: approve
        type: ask
        interaction:
          mode: select
          message: Approve this plan?
          artifact: plan
          comment:
            policy: optional
            message: What should change?
          options:
            - { value: approve, label: Approve, comment: disabled }
            - { value: revise, label: Request changes, comment: required }
            - { value: deny, label: Deny, fail: true }

  - id: implement
    name: Implement the approved plan
    type: agent
    prompt: "Implement this plan for: ${{ inputs.feature }}\n{{ artifacts.plan.content }}"
    tools:
      allow:
        - capability: read
        - capability: write
        - capability: shell
    timeout_seconds: 300
```

<div class="grid cards" markdown>

- **[Learn](getting-started/installation.md)**

    Install Dovo and run your first blueprint.

- **[Recipes](recipes/feature-development-loop.md)**

    Complete blueprints you can copy.

- **[Reference](reference/blueprint-schema.md)**

    Schemas and every `dovo` command.

</div>
