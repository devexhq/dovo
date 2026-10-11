# Blueprint Schema Reference

YAML schema for blueprint files.

---

## Root Fields

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `name` | `string` | No | Catalog key | Display name. Defaults to the catalog key. |
| `description` | `string` | No | `""` | Description of the blueprint. |
| `summary` | `string` | No | `""` | One-line summary shown in `dovo blueprint list`. |
| `version` | `integer \| string` | No | `1` | Schema version. |
| `use_worktree` | `boolean` | No | `true` | When `true`, the run uses an isolated Git worktree on a `dovo/dovo_*` branch. |
| `timeout_seconds`| `integer` | No | `null` | Currently has no effect. |
| `env` | `map[string, string]` | No | `{}` | Currently has no effect. |
| `inputs` | `map[string, ParameterInput]` | No | `{}` | Parameter inputs accepted by the blueprint. See [Inputs Schema](inputs-schema.md). |
| `defaults` | `BlueprintDefaults` | No | `{}` | Defaults inherited by steps. |
| `steps` | `list[Step \| Loop]` | No | `[]` | Ordered list of steps to execute. See [Step Schema](step-schema.md). |

---

## `defaults` Object

Values here apply to any step that does not set its own:

| Field | Type | Default | Description |
|---|---|---|---|
| `on_failure` | `string \| FailureSpec` | `null` | Failure policy for steps without their own `on_failure`. |

---

## Location

Blueprints live in `.dovo/catalog/blueprints/` and can contain steps and loop blocks (`type: loop`).

---

## Example

```yaml
name: full-verification-flow
description: End-to-end code generation, testing, and validation workflow
summary: Verify codebase and run full regression suite
version: 1
use_worktree: true
timeout_seconds: 600

env:
  NODE_ENV: test
  CI: "true"

inputs:
  suite:
    type: string
    description: Target test suite name
    default: unit
    aliases: ["-s", "--suite"]

defaults:
  on_failure:
    action: retry
    max_retries: 2
    backoff_ms: 1000
    on_max_retries: prompt_user

steps:
  - id: setup
    name: Install dependencies
    run: uv sync --all-extras

  - id: run-suite
    name: Run specified test suite
    run: pytest tests/${{ inputs.suite }}
    assert:
      exit_code: 0
```
