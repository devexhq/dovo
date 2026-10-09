# Failure Handling & Session Resumption

Dovo provides declarative quality assertions and durable run state so that long-running blueprints can recover gracefully from failures.

---

## Failure Handling Policies (`on_failure`)

Each step (or blueprint `defaults:`) can specify an `on_failure` directive to control execution flow when a step encounters an error or fails an assertion.

### Policy Vocabulary

| Policy | Behavior |
|---|---|
| `abort` *(default)* | Terminates execution immediately and marks the session as failed. |
| `continue` | Ignores the step failure, marks the step as ignored, and proceeds to the next step. |
| `retry` | Retries the step locally up to `max_retries` attempts before escalating to `on_max_retries`. |
| `prompt_user` | Prompts the user interactively in the terminal to choose: Retry, Continue, or Abort. |

---

## Configuring `on_failure`

You can supply a bare policy name or a detailed retry specification:

### 1. Simple String Shorthand

```yaml
steps:
  - id: lint
    run: ruff check .
    on_failure: continue
```

### 2. Detailed Retry & Escalation Object

```yaml
steps:
  - id: download-dependencies
    run: uv sync
    on_failure:
      action: retry
      max_retries: 3
      backoff_ms: 1000
      on_max_retries: prompt_user
```

*Note: `on_max_retries` must be a terminal policy (`abort`, `continue`, or `prompt_user`).*

---

## Step Quality Assertions (`assert:`)

Steps can declare criteria that must pass for the step to be considered successful. If an assertion fails, the step is marked as failed even if the process exited with code `0`.

```yaml
steps:
  - id: run-pytest
    name: Verify test results
    run: pytest --json-report --json-report-file=report.json
    assert:
      exit_code: 0
      output_contains: "0 errors"
      output_not_contains: ["FATAL", "PANIC"]
      regex_match: "([0-9]+) passed"
      json_match:
        path: "summary.status"
        operator: "eq"
        value: "APPROVED"
      file_exists: "report.json"
      file_not_empty: "report.json"
```

For full details on assertion operators, see the [Assertions Schema Reference](../reference/assertions-schema.md).

---

### Agent step failures

A failed agent attempt records exit code `201` (`unfixable`), `202` (`timeout`), `203` (`provider_error`), or `204` (`blocked`) together with its JSON summary on stdout, and both are kept across `dovo resume`. See [Agent step outcomes](../reference/step-schema.md#agent-step-outcomes) for the full mapping.

- `on_failure: continue` records the step as `ignored` with exit code `0`, so `steps.<id>.exit_code` no longer shows the failure. The stdout summary keeps the original status, so branch on `steps.<id>.outputs.status`.
- Retries follow the authored `on_failure` policy only. A `no_op` completes the step and is never retried.
- A failed `assert:` fails the step but leaves the JSON status unchanged.

---

## Interactive Prompts & Durable Run State

When a step fails with `on_failure: prompt_user`:
1. Dovo pauses the execution loop.
2. The paused step and its failed attempt are persisted as **durable run state** in the centralized database.
3. The user is prompted interactively:
   ```text
   Step 'verify-tests' failed (exit code: 1).
   [r]etry / [c]ontinue / [a]bort ?
   ```

### Durable Run State Contents
The session row owns the execution state and preserves:
- The paused step and the failed attempt that triggered the prompt.
- The results of every completed step.
- The worktree identifier of the retained worktree.
- Resolved parameter input values and run options (`--keep`, `--agent`, `--auto-apply`).

`session.json` in the session directory is a projection of this state (manifest, execution tree, lifecycle, flattened results), regenerated from the row.

---

## Resuming Sessions (`dovo resume`)

If you exit or interrupt an interactive session (or if a prompt is left unresolved), the worktree remains preserved. You can resume execution from the exact point of failure using `dovo resume`:

```bash
# Resume by session ID
dovo resume blueprint_a1b2c3d4
```

Dovo reloads the retained worktree and re-enters the failure prompt for the paused step using its recorded failed attempt, without re-running the failed command or any earlier completed step. Choosing retry starts the next attempt; continue and abort finish the step as ignored or failed.

### Resuming a paused loop

A loop is part of the same durable run state: the paused loop, its current iteration, and the paused body step are all persisted. `dovo resume` re-enters the paused body step's prompt from its recorded failed attempt. It never re-runs finished body steps or earlier iterations, and it runs only the remaining body steps of the current iteration. The loop's `until` conditions are evaluated over every body step of that iteration, including those that finished before the pause. Granted iterations are persisted with the loop, so a resumed loop keeps its raised ceiling.

---

## Non-Interactive & CI/CD Execution

In automated environments (such as CI pipelines or background cron jobs), interactive prompts cannot block on standard input.

Pass the `--no-tty` flag:

```bash
dovo run test-suite --no-tty
```

When `--no-tty` is enabled, any `prompt_user` policy automatically degrades to `abort` and emits a warning.

---

## Next Steps

- Check out the [AI Agent Providers Guide](agent-providers.md).
- Read the [Assertions Schema Reference](../reference/assertions-schema.md).
