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

A failed agent attempt records its error code and an exit code derived from it: `201` for `AGENT_TASK_UNFIXABLE`, `202` for `AGENT_TIMEOUT`, `204` for `AGENT_TASK_BLOCKED` and `203` for any other code. Both are kept across `dovo resume`, together with the JSON summary on stdout, whose `status` is `null` for a failed attempt. See [Agent step outcomes](../reference/step-schema.md#agent-step-outcomes) for the full mapping.

- `on_failure: continue` records the step as `ignored` and keeps the failed attempt's exit code, so an `AGENT_TASK_UNFIXABLE` attempt still records `201`. `exit_code` is the process's own code: a step failed by an `assert:` check or a missing required artifact keeps `0` when its process exited cleanly, and only `status` shows that failure. The step keeps its error code, so branch on `steps.<id>.error_code`.
- Retries follow the authored `on_failure` policy only. A `no_op` completes the step and is never retried.
- A failed `assert:` fails the step but leaves the JSON status of a completed attempt unchanged.

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
- Resolved parameter input values and run options (`--keep`, `--discard`, `--agent`, `--auto-apply`).

`session.json` in the session directory is a generated copy of this state.

---

## Interrupting a Run

Ctrl-C, `SIGTERM` or closing the terminal (`SIGHUP`) while a step runs stops the processes of every step and agent the run started: each gets `SIGTERM`, then `SIGKILL`, and Dovo waits up to 5 seconds for them to exit. Only then is the run recorded `cancelled`, so a cancelled session has nothing left editing its worktree. An interrupt while no step process runs is recorded `cancelled` at once. Either way Dovo releases the workspace lock before it exits.

When a process group is still running after that wait, the run and the interrupted step are recorded `failed`, never `cancelled`, with `SESSION_PROCESS_STOP_FAILED`:

```text
Run interrupted, but process group 48213 is still running.
Fix:
- Its processes may still be running; stop them with 'kill -9 -48213'
```

The run still exits as an interrupted run does, so read `error_code` in `--format json` output to tell it from a clean cancel.

---

## Resuming Sessions (`dovo resume`)

A run can also pause at an `ask` step that waits for a person's answer; see [Human Input](human-input.md).

A run that ends `failed` also keeps its worktree and branch, so you can inspect what the failed step left behind. The run summary prints the path, and `dovo worktree delete <worktree-id>` removes it when you are done; `--discard` removes it automatically. See [worktree retention](../cli/run.md#worktree-retention).

If you exit an interactive run or leave a prompt unanswered, the worktree is kept. Resume from the point of failure with `dovo resume`:

```bash
# Resume by session ID
dovo resume blueprint_a1b2c3d4
```

Dovo reopens the kept worktree and shows the failure prompt for the paused step again. It does not re-run the failed command or any completed step. Retry starts the next attempt; continue and abort finish the step as ignored or failed.

### Resuming a run whose process died

When the `dovo` process running a blueprint dies (it is killed, or the machine restarts), its session is left `running`. `dovo resume` and `dovo session resume` settle such sessions first: a session that saved its progress, whose worktree still exists if it used one, and none of whose processes is still running is paused, and every other one is marked `failed`. The resume prints `Paused 1 interrupted session; continue it with 'dovo resume <session_id>'.` and then resumes the session you named, or, with no session ID, the latest paused session, which can be the one just paused.

The step that was running when the process died has no recorded result, so it runs again from its start as a fresh attempt with the same attempt number, and the resume prints:

```text
Step '<step_id>' was interrupted before it recorded a result and was run again as attempt <n>; check for side effects its interrupted attempt already made.
```

The interrupted attempt may already have written files, pushed a branch or called an external service before it died; check for those side effects before relying on the step's new result. Steps that had finished do not run again.

Resume a crashed run from the checkout that created it: from another checkout, its worktree is not found and the resume exits `1`.

If every step had finished before the process died, the resume runs no step: the run ends `completed`, and a run started with `--auto-apply` applies its changes to your checkout. If every step had finished but one of them failed, the run is marked `failed` with `Session interrupted by abnormal process termination` and the resume exits `1`.

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
