# Step Schema Reference

This reference documents the YAML specification for Step definitions and Loop Step blocks in Worktree.

---

## `StepDefinition` Fields

Every standard step accepts the following fields:

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `id` | `string` | **Yes** | Auto-generated | Unique step identifier. Auto-derived from `name` (slugified) if omitted in blueprint. |
| `name` | `string` | No | `null` | Display name shown during execution progress. |
| `description` | `string` | No | `null` | Optional description of the step's operation. |
| `run` | `string` | Conditional | `null` | Shorthand shell command. It cannot be combined with `uses` or inline-type-only fields. |
| `uses` | `string` | Conditional | `null` | Reference to a reusable step ID (`wt/*` or catalog step). The current model does not enforce exclusivity with other mode-specific fields. |
| `type` | `string` | Conditional | `null` | Primitive type: `command`, `agent`, or `script`. |
| `command` | `string` | Conditional | `null` | Shell command string. Required when `type: command`. |
| `prompt` | `string` | Conditional | `null` | Instruction sent to the agent provider after interpolation; it must not be blank. Required when `type: agent`. |
| `script_path` | `string` | Conditional | `null` | Relative path to local script. Required when `type: script`. |
| `tools` | `list[string]` | No | `[]` | Accepted agent-step metadata; it is not enforced and does not restrict what the provider can do. |
| `env` | `map[string, string]` | No | `{}` | Step-specific environment variables. Supports `${{ inputs.* }}` interpolation. |
| `timeout_seconds`| `integer` | No | `120` | Maximum execution duration (seconds, $> 0$). |
| `assert` | `StepAssert` | No | `null` | Verification criteria. See [Assertions Schema](assertions-schema.md). |
| `on_failure` | `string \| FailureSpec` | No | `abort` | Failure handling policy or detailed retry object. |

---

## Step Shape Validation Rules

Worktree strictly validates step configurations:
1. **Resolution Order**: A step must specify at least one of `run`, `uses`, or `type`; the model checks `run`, then `uses`, then `type`.
2. **`run` Exclusions**: When `run` is used, it cannot be combined with `uses`, `command`, `type`, `prompt`, `script_path`, or `tools`.
3. **`uses` Boundary**: When `uses` is present without `run`, the current model does not enforce exclusivity with other mode-specific fields.
4. **Type Field Requirements**:
   - `type: command` $\rightarrow$ requires `command`
   - `type: agent` $\rightarrow$ requires `prompt`
   - `type: script` $\rightarrow$ requires `script_path`

---

## `on_failure` FailureSpec Object

When configuring detailed failure handling, `on_failure` can be specified as a mapping:

| Field | Type | Default | Allowed Values / Bounds | Description |
|---|---|---|---|---|
| `action` | `string` | `abort` | `abort`, `continue`, `prompt_user`, `retry` | Initial action when a step fails. |
| `max_retries` | `integer` | `3` | $\ge 1$ | Maximum number of retry attempts (when `action: retry`). |
| `backoff_ms` | `integer` | `0` | $\ge 0$ | Milliseconds to sleep between retry attempts. |
| `on_max_retries` | `string` | `abort` | `abort`, `continue`, `prompt_user` | Terminal policy when all retry attempts are exhausted. |

---

## `LoopStepBlock`

A generic-blueprint composite step block that repeats a list of steps in `do` until an `until` condition is met:

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `id` | `string` | **Yes** | — | Unique identifier for the loop block. |
| `type` | `string` | **Yes** | `loop` | Must be `loop`. |
| `max_iterations`| `integer` | No | `5` | Maximum number of iterations ($\ge 1$). |
| `until` | `list[string]` | **Yes** | — | Termination condition expressions (e.g. `['steps.test.exit_code == 0']`). |
| `do` | `list[StepDefinition]` | **Yes** | — | List of steps to execute sequentially on each iteration. |
| `on_max_iterations` | `string` | No | `prompt_user` | Terminal policy (`abort`, `continue`, `prompt_user`) if loop reaches `max_iterations` without terminating. |

---

## Runtime Execution Metadata & Environment Variables

Step executions receive structured runtime context through `WT_*` environment variables and template interpolation paths:

### Environment Variables

| Variable | Source Path | Description |
|---|---|---|
| `WT_STEP_ID` | `step.id` | Current step ID |
| `WT_STEP_NAME` | `step.name` | Current step display name |
| `WT_STEP_INDEX` | `step.index` | 1-based index of current step |
| `WT_STEP_ATTEMPT` | `step.attempt` | 1-based attempt count for this step execution |
| `WT_ITERATION_INDEX` | `iteration.index` | 1-based loop iteration index |
| `WT_BLUEPRINT_NAME` | `blueprint.name` | Name of running blueprint (or empty) |
| `WT_BLUEPRINT_SHA` | `blueprint.sha` | Blueprint catalog key (or empty) |
| `WT_PREVIOUS_STEP_ID` | `previous_step.id` | Step ID of immediately prior step (or empty) |
| `WT_PREVIOUS_STEP_NAME` | `previous_step.name` | Step name of immediately prior step (or empty) |
| `WT_PREVIOUS_STEP_INDEX` | `previous_step.index` | 1-based index of immediately prior step (or empty) |
| `WT_PREVIOUS_STEP_STATUS` | `previous_step.status` | Recorded status of immediately prior step (`completed`, `failed`, `ignored`, or empty) |
| `WT_PREVIOUS_STEP_EXIT_CODE` | `previous_step.exit_code` | Decimal exit code of immediately prior step (or empty) |
| `WT_STEPS_JSON` | `steps` | JSON array of finished step objects (`[{"id": "...", "name": "...", "index": "...", "status": "...", "exit_code": "..."}]`) — step outputs are never included here (see `outputs.<key>` interpolation below) |
| `WT_TEMP` | `tmp.session_dir` | Session scratch directory, shared across all steps in the run (empty when no session ID is set) |
| `WT_RUNNER_TEMP` | `tmp.session_dir` | Alias for `WT_TEMP`, for GitHub Actions parity |
| `WT_STEP_TEMP` | `tmp.step_dir` | Step-specific scratch subdirectory under `WT_TEMP` |
| `WT_OUTPUT` | `tmp.output_file` | Path to append `key=value` (or heredoc) lines that become this step's `outputs` |

### Interpolation Paths

- **Current step**: `{{ step.id }}`, `{{ step.name }}`, `{{ step.index }}`, `{{ step.attempt }}`
- **Blueprint**: `{{ blueprint.name }}`, `{{ blueprint.sha }}`. `task.*` and `workflow.*` are legacy aliases; use `blueprint.*` in new documents.
- **Previous step**: `{{ previous_step.id }}`, `{{ previous_step.name }}`, `{{ previous_step.index }}`, `{{ previous_step.status }}`, `{{ previous_step.exit_code }}`
- **Historical steps (`steps`)**:
  - `{{ steps[0].<field> }}`: 0-based indexing for finished steps in run order.
  - `{{ steps[-1].<field> }}`: Python-style negative index (`-1` is the last finished step; matches `previous_step`).
  - `{{ steps.<id>.<field> }}` / `{{ steps['<id>'].<field> }}`: Keyed access by completed step ID.
  - Valid historical fields: `id`, `name`, `index` (1-based ordinal), `status`, `exit_code`.
  - `{{ steps.<id>.outputs.<key> }}` / `{{ steps['<id>'].outputs.<key> }}`: A specific output value a completed step wrote to `$WT_OUTPUT`. An unknown step ID or output key resolves to an empty string.
  - The in-flight current step is never present in `steps`. Out-of-range indices or unknown step IDs safely resolve to empty strings.

### Step Outputs (`$WT_OUTPUT`)

A step can write `key=value` lines to the file at `$WT_OUTPUT` to expose values to later steps:

```bash
echo "greeting=hello" >> "$WT_OUTPUT"
```

Blank lines and lines starting with `#` are ignored. A line missing `=` is skipped with a warning rather than failing the step. `$WT_OUTPUT` is truncated before each attempt, so only the final attempt's writes are kept.

For a multi-line value, use the heredoc form (mirrors GitHub Actions' `$GITHUB_OUTPUT`):

```bash
echo "body<<EOF_random_delimiter" >> "$WT_OUTPUT"
echo "line one" >> "$WT_OUTPUT"
echo "line two" >> "$WT_OUTPUT"
echo "EOF_random_delimiter" >> "$WT_OUTPUT"
```

Everything between the opening `key<<DELIM` line and the matching `DELIM` terminator line is captured verbatim (no `#`/blank-line/`=` handling applied inside the block) and joined with `\n`. Choose a delimiter unlikely to appear in the body itself, such as a random or UUID string — the parser treats any body line that exactly matches the delimiter as the terminator, with no escape mechanism. A heredoc missing its terminator drops the key and records a warning rather than failing the step.

### Precedence
1. Explicit step `env` key
2. `WT_*` metadata env
3. Ambient process env
