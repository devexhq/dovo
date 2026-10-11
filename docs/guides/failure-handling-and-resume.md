# Failure Handling & Session Resumption

Use `on_failure` policies and `assert:` checks to control what happens when a step fails, and `dovo resume` to continue a paused run.

---

## Failure Handling Policies (`on_failure`)

Set `on_failure` on a step, or in the blueprint's `defaults:`, to control what happens when a step errors or fails an assertion.

### Policy Vocabulary

| Policy | Behavior |
|---|---|
| `abort` *(default)* | Terminates execution immediately and marks the session as failed. |
| `continue` | Ignores the step failure, marks the step as ignored, and proceeds to the next step. |
| `retry` | Retries the step locally up to `max_retries` attempts before escalating to `on_max_retries`. |
| `prompt_user` | Prompts the user interactively in the terminal to choose: Retry, Continue, or Abort. |

---

## Configuring `on_failure`

Use a policy name or a retry object:

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

Assertions are checks a step must pass. A failed assertion fails the step even if the process exited with `0`.

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

See the [Assertions Schema](../reference/assertions-schema.md) for all operators.

---

### Agent step failures

A failed agent attempt records exit code `201` (`unfixable`), `202` (`timeout`), `203` (`provider_error`), or `204` (`blocked`) together with its JSON summary on stdout, and both are kept across `dovo resume`. See [Agent step outcomes](../reference/step-schema.md#agent-step-outcomes) for the full mapping.

- `on_failure: continue` records the step as `ignored` with exit code `0`, so `steps.<id>.exit_code` no longer shows the failure. The stdout summary keeps the original status, so branch on `steps.<id>.outputs.status`.
- Retries follow the authored `on_failure` policy only. A `no_op` completes the step and is never retried.
- A failed `assert:` fails the step but leaves the JSON status unchanged.

---

## Interactive Prompts & Saved Run State

When a step fails with `on_failure: prompt_user`:
1. Dovo pauses the execution loop.
2. The paused step and its failed attempt are saved as run state.
3. The user is prompted interactively:
   ```text
   Step 'verify-tests' failed (exit code: 1).
   [r]etry / [c]ontinue / [a]bort ?
   ```

### What is saved
The saved run state includes:
- The paused step and the failed attempt that triggered the prompt.
- The results of every completed step.
- The worktree identifier of the retained worktree.
- Resolved parameter input values and run options (`--keep`, `--agent`, `--auto-apply`).

`session.json` in the session directory is a generated copy of this state.

---

## Resuming Sessions (`dovo resume`)

If you exit an interactive run or leave a prompt unanswered, the worktree is kept. Resume from the point of failure with `dovo resume`:

```bash
# Resume by session ID
dovo resume blueprint_a1b2c3d4
```

Dovo reopens the kept worktree and shows the failure prompt for the paused step again. It does not re-run the failed command or any completed step. Retry starts the next attempt; continue and abort finish the step as ignored or failed.

### Resuming a paused loop

The paused loop, its current iteration and the paused body step are all saved. `dovo resume` shows the paused step's prompt again, then runs only the remaining body steps of the current iteration. Finished steps and earlier iterations are not re-run. The `until` conditions consider every body step of that iteration, including those that finished before the pause. Extra iterations you granted are saved, so a resumed loop keeps its raised limit.

---

## Non-Interactive & CI/CD Execution

In CI or other unattended runs there is no one to answer a prompt. Pass `--no-tty`:

```bash
dovo run test-suite --no-tty
```

With `--no-tty`, any `prompt_user` policy becomes `abort` and a warning is printed.

---

## Next Steps

- [AI Agent Providers](agent-providers.md)
- [Assertions Schema](../reference/assertions-schema.md)
