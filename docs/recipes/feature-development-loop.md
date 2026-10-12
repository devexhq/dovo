# Recipe: Feature Development Loop

A blueprint that plans a feature and revises the plan until a person approves it, implements the approved plan, then reviews the result and lets the person pick which findings to fix. Agent steps run in the worktree, so the loop needs a Git worktree. See [Agent Providers](../guides/agent-providers.md) and [Human Input](../guides/human-input.md).

---

## The Blueprint

Create `.dovo/catalog/blueprints/feature.yml`:

```yaml
name: feature
description: Plan a feature until approved, implement it, then triage review findings
summary: Plan, approve, implement and triage loop
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

  - id: review-loop
    name: Review and triage
    type: loop
    max_iterations: 3
    until:
      - steps.triage.outputs.selected_count == 0
    on_max_iterations: prompt_user
    do:
      - id: review
        type: agent
        prompt: 'Review the changes against the plan. Write review.json: {"findings":[{"id","severity","summary","detail"}]}. Change nothing else.'
        tools:
          allow:
            - capability: read
            - capability: write
        artifacts:
          - name: review
            path: review.json
        timeout_seconds: 240

      - id: triage
        type: ask
        interaction:
          mode: multiselect
          message: Which findings should the agent fix?
          options_from:
            artifact: review
            file: review.json
            items_path: findings
            value_field: id
            label_field: summary
          comment:
            policy: optional

      - id: fix
        type: agent
        prompt: |
          Fix only these findings: {{ steps.triage.outputs.selected }}
          Details: {{ artifacts.review.content }}
          Notes: {{ steps.triage.outputs.comment }}
          If the list is empty, change nothing.
        tools:
          allow:
            - capability: read
            - capability: write
            - capability: shell
        timeout_seconds: 300

  - id: cleanup
    name: Remove the working files
    run: rm -f PLAN.md review.json
```

---

## Running the Recipe

```bash
dovo run feature --feature "Add a --json flag to the export command"
```

### What happens

1. `plan` writes `PLAN.md`, which is published as the `plan` artifact, and `approve` shows it. Choose `revise` with a comment and the loop plans again with your feedback; choose `approve` and the loop ends. `deny` fails the run.
2. `implement` builds the approved plan.
3. `review` writes its findings to `review.json`. `triage` lists them and you pick the ones to fix. `fix` addresses only those.
4. Selecting nothing makes `selected_count` `0` and ends the loop; otherwise it reviews again, up to `max_iterations`.
5. `cleanup` deletes the working files so they are not part of the diff.

Run it with `--no-tty` to pause at each prompt and answer with `dovo resume --answer`, as described in [Human Input](../guides/human-input.md#headless-runs). Review the result with `dovo diff`, then apply it with `dovo worktree apply`.
