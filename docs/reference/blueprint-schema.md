# Blueprint Schema Reference

This reference documents the complete YAML schema for generic blueprint definitions in Dovo.

---

## Root Fields

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `name` | `string` | No | Catalog key | Unique display name of the blueprint; when omitted, it defaults to the catalog key. |
| `description` | `string` | No | `""` | Detailed description of the blueprint's goal and behavior. |
| `summary` | `string` | No | `""` | Short single-line description shown in `dovo blueprint list` output. |
| `version` | `integer \| string` | No | `1` | Blueprint schema format version. |
| `use_sandbox` | `boolean` | No | `true` | When `true`, execution normally runs in an isolated Git worktree branch (`worktree/sandbox-*`). |
| `timeout_seconds`| `integer` | No | `null` | Accepted metadata; the current runtime does not apply it as an overall timeout. |
| `env` | `map[string, string]` | No | `{}` | Accepted metadata; the current runtime does not inject it into child step environments. |
| `inputs` | `map[string, ParameterInput]` | No | `{}` | Parameter inputs accepted by the blueprint. See [Inputs Schema](inputs-schema.md). |
| `defaults` | `BlueprintDefaults` | No | `{}` | Shared defaults inherited by child steps. |
| `steps` | `list[Step \| Loop]` | No | `[]` | Ordered list of steps to execute. See [Step Schema](step-schema.md). |

---

## `defaults` Object

The `defaults` object defines blueprint-level fallback directives inherited by any child step that omits its own configuration:

| Field | Type | Default | Description |
|---|---|---|---|
| `on_failure` | `string \| FailureSpec` | `null` | Default failure handling policy copied to steps that do not define an explicit `on_failure`. |

---

## Generic Blueprint Structure

A blueprint is stored under `.dovo/catalog/blueprints/` and may contain standard steps and loop step blocks (`type: loop`). The catalog has no task or workflow kind.

---

## Full Example Specification

```yaml
name: full-verification-flow
description: End-to-end code generation, testing, and validation workflow
summary: Verify codebase and run full regression suite
version: 1
use_sandbox: true
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
