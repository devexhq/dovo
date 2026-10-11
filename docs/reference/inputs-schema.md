# Parameter Inputs & Template Schema Reference

Schema for blueprint inputs and `${{ inputs.<name> }}` placeholders.

---

## `ParameterInput` Fields

Each entry in a blueprint's `inputs:` mapping accepts the following fields:

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `type` | `string` | No | `string` | Data type: `string`, `boolean`, or `integer`. |
| `description` | `string` | No | `null` | What the input is for. |
| `required` | `boolean` | No | `false` | When `true`, execution fails if no value is provided and no `default` exists. |
| `default` | `string \| int \| bool` | No | `null` | Value used when none is given. |
| `aliases` | `list[string] \| string` | No | `[]` | CLI flag aliases (e.g. `["-b", "--branch"]`). A single string is coerced to a 1-element list. |

---

## Supported Types & Coercion Rules

| Type Identifier | Valid Values / Coercion Rules |
|---|---|
| `string` | Any textual value. |
| `boolean` | `true`, `false`, `1`, `0`, `yes`, `no`, `on`, `off` (case-insensitive). |
| `integer` | Valid signed integers (e.g. `10`, `-1`). |

---

## CLI Flag Mapping

CLI arguments map to inputs in three ways:

1. **Declared Aliases**:
   ```yaml
   inputs:
     target:
       type: string
       aliases: ["-t", "--target"]
   ```
   Can be passed as:
   ```bash
   dovo run my-blueprint --target src/main.py
   dovo run my-blueprint -t src/main.py
   ```

2. **Generic `-i` / `--input` Flag**:
   ```bash
   dovo run my-blueprint -i target=src/main.py -i retries=3
   ```

3. **Bare Boolean Flags**:
   If an input is of type `boolean` with alias `--verbose`, passing `--verbose` sets the value to `true` without requiring an explicit `=true` value.

---

## Template Expression Syntax

Reference an input inside a step with:

```text
${{ inputs.<name> }}
```

### Interpolation Scope
Placeholders are replaced in:
* `run` string values
* `command` string values
* `prompt` string values
* `script_path` string values
* String values within step `env` dictionaries

### Unresolved Placeholders
If a template placeholder references an identifier not declared in `inputs:`, it is left as literal text, with no error.
