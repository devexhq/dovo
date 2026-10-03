# Parameter Inputs & Expressions

Dovo allows blueprint authors to define dynamic parameter inputs. Inputs can be customized from the command line and referenced inside step commands, agent prompts, script paths, and environment variables using `${{ inputs.<name> }}` interpolation placeholders.

---

## Declaring Inputs in Blueprints

Inputs are declared under the top-level `inputs:` map in your blueprint YAML file:

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

When executing a blueprint with `dovo run`, you can supply input values using declared aliases or generic input flags:

### 1. Using Declared Aliases
Pass arguments directly using any of the alias flags declared in the blueprint:

```bash
dovo run test-runner --target tests/unit -v --retries 3
```

### 2. Using Generic `-i` / `--input` Overrides
Supply key-value pairs using the generic `-i` or `--input` options:

```bash
dovo run test-runner -i target=tests/integration -i verbose=true -i api_token=secret123
```

### 3. Boolean Flag Shorthand
For boolean inputs, passing the bare flag sets the value to `true`:

```bash
# Sets verbose=true
dovo run test-runner --verbose
```

---

## Template Interpolation Syntax

Inputs and runtime execution metadata are referenced using the `${{ <namespace>.<name> }}` or `{{ <namespace>.<name> }}` placeholder syntax.

### Supported Fields for Interpolation
Interpolation is evaluated at runtime in the following step fields:
* `run`: `run: pytest ${{ inputs.target }}`
* `command`: `command: npm test -- --path=${{ inputs.path }}`
* `prompt`: `prompt: "Fix the bug in ${{ inputs.module }} according to issue ${{ inputs.issue_id }}"`
* `script_path`: `script_path: scripts/${{ inputs.script_name }}.py`
* `env`: String values inside step `env:` blocks.

### Interpolation Namespaces & Behavior
* **Inputs**: `${{ inputs.<name> }}` or `{{ inputs.<name> }}` evaluates declared blueprint parameter values.
* **Execution Metadata**: `step.*`, `blueprint.*`, `previous_step.*`, and historical `steps[...]` / `steps.<id>.*` evaluate runtime execution properties (see [Working with Steps](working-with-steps.md#runtime-execution-metadata--environment-variables)). `task.*` and `workflow.*` are legacy aliases; use `blueprint.*` in new documents.
* If a placeholder references an unknown name, the placeholder is preserved verbatim as literal text.


---

## Input Validation & Error Handling

If a required input is missing or fails type validation, Dovo reports a structured error before creating the sandbox:

```text
Error: Missing required input 'api_token' for blueprint 'test-runner'.

Usage:
  dovo run test-runner -i api_token=<value>
```

---

## Next Steps

- Learn about [Failure Handling & Resumption](failure-handling-and-resume.md).
- View the [Inputs Schema Reference](../reference/inputs-schema.md).
