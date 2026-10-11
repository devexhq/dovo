# Parameter Inputs & Expressions

A blueprint can declare inputs that you set on the command line. Reference them in commands, agent prompts, script paths and environment variables with `${{ inputs.<name> }}`.

---

## Declaring Inputs in Blueprints

Declare inputs under the top-level `inputs:` map:

```yaml
name: test-runner
description: Run test suites with configurable target and verbosity

inputs:
  target:
    type: string
    description: Target test file or directory
    default: tests/
    aliases: ["-t", "--target"]

  verbose:
    type: boolean
    description: Enable verbose pytest output
    default: false
    aliases: ["-v", "--verbose"]

  retries:
    type: integer
    description: Number of test attempts
    default: 1
    aliases: ["-r", "--retries"]

  api_token:
    type: string
    description: Authentication token
    required: true

steps:
  - id: run-pytest
    name: Run Pytest
    run: pytest ${{ inputs.target }}
    env:
      TEST_RETRIES: "${{ inputs.retries }}"
      API_TOKEN: "${{ inputs.api_token }}"
```

---

## Supported Input Types

| Type | YAML Syntax | Coercion Rules | Example Default |
|---|---|---|---|
| `string` | `type: string` | Text values. | `"tests/"` |
| `boolean` | `type: boolean` | Parses `true`/`false`, `1`/`0`, `yes`/`no`. | `false` |
| `integer` | `type: integer` | Numeric integers. | `3` |

---

## Passing Inputs via CLI

`dovo run` accepts input values as declared aliases or as generic `-i` flags.

### 1. Using Declared Aliases
Use any alias declared in the blueprint:

```bash
dovo run test-runner --target tests/unit -v --retries 3
```

### 2. Using Generic `-i` / `--input` Overrides
Pass `key=value` pairs:

```bash
dovo run test-runner -i target=tests/integration -i verbose=true -i api_token=secret123
```

### 3. Boolean Flag Shorthand
A bare boolean flag sets the value to `true`:

```bash
# Sets verbose=true
dovo run test-runner --verbose
```

---

## Template Interpolation Syntax

Inputs and run metadata use `${{ <namespace>.<name> }}` or `{{ <namespace>.<name> }}`.

### Supported Fields for Interpolation
Placeholders are replaced in these step fields:
* `run`: `run: pytest ${{ inputs.target }}`
* `command`: `command: npm test -- --path=${{ inputs.path }}`
* `prompt`: `prompt: "Fix the bug in ${{ inputs.module }} according to issue ${{ inputs.issue_id }}"`
* `script_path`: `script_path: scripts/${{ inputs.script_name }}.py`
* `env`: String values inside step `env:` blocks.

### Interpolation Namespaces & Behavior
* **Inputs**: `${{ inputs.<name> }}` or `{{ inputs.<name> }}` insert declared input values.
* **Execution Metadata**: `step.*`, `blueprint.*`, `previous_step.*`, and historical `steps[...]` / `steps.<id>.*` insert run-time properties (see [Working with Steps](working-with-steps.md#runtime-execution-metadata--environment-variables)). `task.*` and `workflow.*` are older aliases; use `blueprint.*`.
* If a placeholder references an unknown name, the placeholder is left as literal text.

---

## Input Validation & Error Handling

If a required input is missing or fails type validation, Dovo reports an error before creating the worktree:

```text
Error: Missing required input 'api_token' for blueprint 'test-runner'.

Usage:
  dovo run test-runner -i api_token=<value>
```

---

## Next Steps

- [Failure Handling & Resumption](failure-handling-and-resume.md)
- [Inputs Schema](../reference/inputs-schema.md)
