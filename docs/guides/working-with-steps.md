# Working with Steps

Steps are the fundamental building blocks of Dovo blueprints. A step executes a shell command or script, resolves a reusable catalog step, or sends a prompt to an agent provider.

---

## Step Execution Modes

Dovo supports three primary ways to define a step:

```text
Step Definition
 ├── 1. Inline Shorthand (run: <cmd>)
 ├── 2. Reusable Catalog Step (uses: <step-name>)
 └── 3. Explicit Inline Step (type: command | agent | script)
```

---

### 1. Inline Shorthand (`run:`)

For simple shell commands, use the concise `run:` key:

```yaml
steps:
  - id: install-deps
    name: Install Python dependencies
    run: uv sync --all-extras

  - id: lint-code
    name: Check code formatting
    run: ruff check .
```

*When `run:` is provided, Dovo automatically maps it to a command step.*

---

### 2. Reusable Catalog Steps (`uses:`)

Steps can be authored as standalone, reusable YAML files in `.dovo/catalog/steps/` and referenced by other blueprints via `uses:`.

```yaml
steps:
  # Reference a built-in curated step
  - id: sync-git
    uses: dovo/git-sync-base

  # Reference a project-local catalog step
  - id: verify-build
    uses: build-and-test
```

#### Curated Built-in Steps (`dovo/*`)
Dovo can provide curated step templates under `dovo/`. Inspect the checked-out catalog before relying on a particular template.

---

### 3. Explicit Inline Steps (`type:`)

For advanced step configuration, use explicit `type:` primitives:

#### A. Command Step (`type: command`)
Executes a shell command with custom timeouts and environment variables:

```yaml
- id: compile-assets
  name: Build Frontend
  type: command
  command: npm run build
  env:
    NODE_ENV: production
  timeout_seconds: 180
```

#### B. Agent Step (`type: agent`)
Sends the interpolated `prompt` to the resolved provider in the active Git worktree and applies any returned change there; it fails without a worktree. `tools` is a structured policy object that is validated but not yet enforced by any provider; omit it to keep the default. A legacy string list is rejected. See [Agent-Step Adapters](agent-providers.md):

```yaml
- id: fix-bug
  name: AI Bug Fixer
  type: agent
  prompt: "Refactor the authentication middleware in src/auth.py to fix issue #${{ inputs.issue_id }}"
  tools:
    allow:
      - capability: read
        pattern: "src/**"
      - capability: write
        pattern: "src/auth.py"
      - capability: shell
        pattern: "git status"
    deny:
      - capability: write
        pattern: ".dovo/**"
  timeout_seconds: 300
```

#### C. Script Step (`type: script`)
Runs an executable script located within your repository:

```yaml
- id: run-validation-script
  name: Custom Validator
  type: script
  script_path: scripts/validate_schema.py
  timeout_seconds: 60
```

---

## Step Attributes Reference

Every step can be configured with the following properties:

| Field | Type | Default | Description |
|---|---|---|---|
| `id` | `string` | Auto-generated | Unique identifier for the step (used in logs and run state tracking). |
| `name` | `string` | `null` | Human-readable title displayed in the execution progress table. |
| `description`| `string` | `null` | Optional description of what the step does. |
| `timeout_seconds` | `integer` | `120` | Maximum duration before the step is terminated. |
| `env` | `map[string, string]` | `{}` | Environment variables specific to this step (supports `${{ inputs.* }}`). |
| `assert` | `StepAssert` | `null` | Declarative quality assertions (exit code, output matchers, file checks). |
| `on_failure` | `string \| FailureSpec` | `abort` | Failure policy (`abort`, `continue`, `retry`, `prompt_user`). |

---

## Loop Step Blocks

Generic blueprints support iterative loops using `type: loop`. Loops repeat a series of nested steps until a condition is met or `max_iterations` is reached:

```yaml
steps:
  - id: tdd-loop
    name: Iterate until tests pass
    type: loop
    max_iterations: 5
    until:
      - steps.step_pytest_verify.exit_code == 0
    on_max_iterations: prompt_user
    do:
      - id: fix-failing-tests
        type: agent
        prompt: "Fix the failing tests under tests/ by changing the implementation."

      - id: step_pytest_verify
        name: Verify test suite
        run: pytest tests/
        assert:
          exit_code: 0
```

---

## Runtime Execution Metadata & Environment Variables

Dovo automatically exposes structured runtime metadata to step commands and templates through process environment variables (`DOVO_*`) and template interpolation (`{{ ... }}` or `${{ ... }}`).

### Environment Variables

Every step execution receives the complete set of `DOVO_*` environment variables. Values are always strings (empty string when not applicable):

| Environment Variable | Source | Description |
|---|---|---|
| `DOVO_STEP_ID` | `step.id` | Unique ID of the current step. |
| `DOVO_STEP_NAME` | `step.name` | Display name of the current step (empty if unset). |
| `DOVO_STEP_INDEX` | `step.index` | 1-based index of this step in the run sequence. |
| `DOVO_STEP_ATTEMPT` | `step.attempt` | 1-based attempt counter (increments on retries and prompt resume). |
| `DOVO_ITERATION_INDEX` | `iteration.index` | 1-based loop iteration index. |
| `DOVO_BLUEPRINT_NAME` | `blueprint.name` | Name of the parent blueprint (empty if unknown). |
| `DOVO_BLUEPRINT_SHA` | `blueprint.sha` | Catalog key of the parent blueprint (empty if unknown). |
| `DOVO_PREVIOUS_STEP_ID` | `previous_step.id` | ID of the immediately prior completed step (empty on first step). |
| `DOVO_PREVIOUS_STEP_NAME` | `previous_step.name` | Name of the immediately prior completed step (empty if unset). |
| `DOVO_PREVIOUS_STEP_INDEX` | `previous_step.index` | 1-based index of the previous step (empty on first step). |
| `DOVO_PREVIOUS_STEP_STATUS` | `previous_step.status` | Recorded status of previous step (`completed`, `failed`, `ignored`). |
| `DOVO_PREVIOUS_STEP_EXIT_CODE` | `previous_step.exit_code` | Decimal exit code of previous step (`0`, `1`, etc.; empty on first step). |
| `DOVO_STEPS_JSON` | `steps` | JSON array of all finished step metadata objects in run order (`[]` when none finished yet); step outputs are never included here — use `{{ steps.<id>.outputs.<key> }}` instead. |
| `DOVO_TEMP` | `tmp.session_dir` | Session scratch directory, shared across every step in the run (empty when no session ID is set). |
| `DOVO_RUNNER_TEMP` | `tmp.session_dir` | Alias for `DOVO_TEMP`, for GitHub Actions parity. |
| `DOVO_STEP_TEMP` | `tmp.step_dir` | Step-specific scratch subdirectory under `DOVO_TEMP`. |
| `DOVO_OUTPUT` | `tmp.output_file` | Path to append `key=value` (or heredoc) lines that become this step's `outputs`. |

### Environment Precedence

When resolving environment variables for step execution:
1. **Explicit step `env`**: Key-value pairs declared under `env:` in the step definition take highest precedence.
2. **`DOVO_*` runtime metadata**: Automatically injected metadata variables.
3. **Ambient process environment**: Process environment variables from the host runner.

### Interpolation Paths

Step fields (`run`, `command`, `prompt`, `script_path`, and `env`) can reference execution metadata using `{{ <namespace>.<field> }}` or `${{ <namespace>.<field> }}` syntax:

* **Current Step**: `{{ step.id }}`, `{{ step.name }}`, `{{ step.index }}`, `{{ step.attempt }}`
* **Blueprint**: `{{ blueprint.name }}`, `{{ blueprint.sha }}`. `task.*` and `workflow.*` are legacy aliases; use `blueprint.*` in new documents.
* **Immediate Previous Step**: `{{ previous_step.id }}`, `{{ previous_step.name }}`, `{{ previous_step.index }}`, `{{ previous_step.status }}`, `{{ previous_step.exit_code }}`
* **Historical Steps (`steps`)**: Access any prior completed step by 0-based index (`steps[0]`), Python-style negative index (`steps[-1]`), or step ID (`steps.<id>` or `steps['<id>']`):
  * `{{ steps[0].id }}`: First completed step ID
  * `{{ steps[-1].status }}`: Most recently finished step status (equivalent to `{{ previous_step.status }}`)
  * `{{ steps.build.exit_code }}`: Exit code of step with `id: build`
  * `{{ steps.build.outputs.artifact_path }}` / `{{ steps['build'].outputs.artifact_path }}`: A specific output value step `build` wrote to `$DOVO_OUTPUT` (see [Step Outputs](#step-outputs-dovo_output) below). An unknown step ID or output key resolves to an empty string.
  * Historical steps contain only completed/finished steps — the in-flight current step is never included in `steps`. Out-of-range indices or unknown step IDs resolve safely to an empty string.

```yaml
steps:
  - id: setup
    name: Setup environment
    run: echo "Initializing..."

  - id: test-with-retry
    name: Run flaky test suite
    run: |
      if [ "$DOVO_STEP_ATTEMPT" -eq 1 ]; then
        exit 1
      else
        echo "Passed on attempt {{ step.attempt }}"
        exit 0
      fi
    on_failure:
      action: retry
      max_retries: 2

  - id: report-status
    name: Report historical outcomes
    run: |
      echo "First step was {{ steps[0].id }} with status {{ steps[0].status }}"
      echo "Test step {{ steps.test-with-retry.id }} exit code: {{ steps.test-with-retry.exit_code }}"
      echo "Previous step {{ previous_step.id }} finished with status {{ steps[-1].status }}"
```

### Step Outputs (`$DOVO_OUTPUT`)

A step can write `key=value` lines to the file at `$DOVO_OUTPUT` to expose values to later steps. Blank lines and lines starting with `#` are ignored, and a malformed line (missing `=`) is skipped with a warning rather than failing the step:

```yaml
steps:
  - id: build
    name: Build artifact
    run: |
      echo "artifact_path=dist/app.tar.gz" >> "$DOVO_OUTPUT"

  - id: publish
    name: Publish built artifact
    run: echo "Publishing {{ steps.build.outputs.artifact_path }}"
```

For a value spanning multiple lines, use the heredoc form (mirrors GitHub Actions' `$GITHUB_OUTPUT`); everything between the `key<<DELIM` line and the matching `DELIM` terminator is captured verbatim and joined with newlines, so pick a delimiter unlikely to collide with the body itself:

```yaml
steps:
  - id: changelog
    name: Collect changelog entries
    run: |
      echo "entries<<CHANGELOG_EOF" >> "$DOVO_OUTPUT"
      git log --oneline -5 >> "$DOVO_OUTPUT"
      echo "CHANGELOG_EOF" >> "$DOVO_OUTPUT"

  - id: notify
    name: Print collected changelog
    run: echo "{{ steps.changelog.outputs.entries }}"
```

---

## Next Steps

- Learn how to pass arguments in [Parameter Inputs & Expressions](passing-inputs.md).
- Configure assertions and retry policies in [Failure Handling & Resumption](failure-handling-and-resume.md).
- Read the [Step Schema Reference](../reference/step-schema.md) for full syntax specifications.
