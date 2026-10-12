# Human Input

An `ask` step pauses a run so a person can approve a plan, give feedback or pick from a list. The answer becomes the step's outputs, which later steps and `until` conditions read. The full field list is in the [`interaction` Block](../reference/step-schema.md#interaction-block).

---

## Approval Gate

Show an artifact, ask for approval, and pass an optional comment to the next step:

```yaml
- id: plan
  type: agent
  prompt: "Write an implementation plan for: ${{ inputs.feature }} to PLAN.md."
  tools: { allow: [{ capability: read }, { capability: write }] }
  artifacts: [{ name: plan, path: PLAN.md }]
- id: approve
  type: ask
  interaction:
    mode: confirm
    message: Approve this plan?
    artifact: plan
    comment: { policy: optional, message: Instructions for the implementer }
- id: implement
  type: agent
  prompt: |
    Implement the approved plan:
    {{ artifacts.plan.content }}
    Instructions: {{ steps.approve.outputs.comment }}
```

A `confirm` prompt without `options` offers `approve` and `deny`; choosing `deny` fails the step and its `on_failure` policy applies.

---

## Revise Until Approved

Put the producer and the ask in a loop. Each iteration sees the previous answer's comment, and an empty string on the first pass:

```yaml
- id: plan-loop
  type: loop
  max_iterations: 3
  until: ['steps.approve.outputs.selected == "approve"']
  on_max_iterations: prompt_user
  do:
    - id: plan
      type: agent
      prompt: |
        Plan: ${{ inputs.feature }}. Write PLAN.md.
        Previous plan: {{ artifacts.plan.content }}
        Feedback to address: {{ steps.approve.outputs.comment }}
      artifacts: [{ name: plan, path: PLAN.md }]
    - id: approve
      type: ask
      interaction:
        mode: select
        message: Approve this plan?
        artifact: plan
        comment: { policy: optional, message: What should change? }
        options:
          - { value: approve, label: Approve, comment: disabled }
          - { value: revise, label: Request changes, comment: required }
          - { value: deny, label: Deny, fail: true }
```

The `until` condition reads the ask step's stdout, a JSON object with the same keys as its outputs. `selected_count` is a JSON integer there, so numeric comparisons work.

---

## Triage Findings

Build the options from a file an earlier step published:

```yaml
- id: review-loop
  type: loop
  max_iterations: 3
  until: ['steps.triage.outputs.selected_count == 0']
  do:
    - id: review
      type: agent
      prompt: 'Review the changes. Write review.json: {"findings":[{"id","severity","summary","detail"}]}.'
      tools: { allow: [{ capability: read }, { capability: write }] }
      artifacts: [{ name: review, path: review.json }]
    - id: triage
      type: ask
      interaction:
        mode: multiselect
        message: Which findings should the agent fix?
        options_from: { artifact: review, file: review.json, items_path: findings, value_field: id, label_field: summary }
        comment: { policy: optional }
    - id: fix
      type: agent
      prompt: |
        Fix only these findings: {{ steps.triage.outputs.selected }}
        Details: {{ artifacts.review.content }}
        Notes: {{ steps.triage.outputs.comment }}
```

The `until` conditions run after the whole iteration, so `fix` also runs once with an empty selection before the loop ends. Tell the agent to change nothing when the list is empty. When the array is empty no prompt is shown at all and the step completes with `selected` set to `[]`.

---

## Answering in the Terminal

In an interactive terminal the prompt is printed and you answer in place: a number picks an option, a comma-separated list picks several, and a second line asks for the comment when the chosen option's policy is `optional` or `required`. Type `/edit` at the comment prompt to use `$VISUAL`, else `$EDITOR`, else multi-line input ended with Ctrl-D. Nothing is recorded until both reads finish.

Press Ctrl-C or close the terminal and the run stays paused with nothing recorded. Continue later with `dovo resume <session_id>`; the stored prompt is asked again without re-resolving options or re-running the step that produced the artifact.

---

## Headless Runs

With `--no-tty`, or with `--format json`, a run that reaches an `ask` step emits the prompt, stays paused and exits `0`:

```bash
dovo run feature -f "Add --json to export" --no-tty
# Run paused at prompt 'approve'. Answer with 'dovo resume blueprint_a1b2c3d4 --answer approve=<value>'.

dovo resume --no-tty --answer approve=revise --comment "Keep config compatible"
```

The answer is validated before any step runs and the run then continues in that process; a later `ask` pauses again. Use `--comment-file` for long comments, or `--answers-file` with a JSON object `{"step_id": "triage", "selected": ["B1", "B3"], "comment": "..."}` (it cannot be combined with `--answer`, `--comment` or `--comment-file`). An invalid answer exits `1` with the valid choices and leaves the session paused:

```text
Error: 'maybe' is not a valid answer for prompt 'approve'.
Fix: use one of: approve, revise, deny
```

A comment supplied for an option whose policy is `disabled` is discarded with a warning. The flags are listed in [`dovo session resume`](../cli/session.md#dovo-session-resume).

---

## Redaction

Comments and text answers are masked with the run's secret redactor before they are stored, logged or shown, the same way step output is. Names listed under `environment.sensitive_variables` in `.dovo/config.json` are masked.
