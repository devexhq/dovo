# Parameter Inputs & Template Schema Reference

This reference documents the parameter input declaration schema and `${{ inputs.<name> }}` template placeholder mechanics in Dovo blueprints.

---

## `ParameterInput` Fields

Each entry in a blueprint's `inputs:` mapping accepts the following fields:

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `type` | `string` | No | `string` | Data type: `string`, `boolean`, or `integer`. |
| `description` | `string` | No | `null` | Explanation of the input parameter. |
| `required` | `boolean` | No | `false` | When `true`, execution fails if no value is provided and no `default` exists. |
| `default` | `string \| int \| bool` | No | `null` | Default fallback value if not specified at runtime. |
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

Dovo maps CLI arguments to declared inputs in two ways:

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

Values are referenced inside steps using:

$$\$\{\{\text{ inputs.<name> }\}\}$$

### Interpolation Scope
Template substitution occurs at execution time for:
* `run` string values
* `command` string values
* `prompt` string values
* `script_path` string values
* String values within step `env` dictionaries

### Unresolved Placeholders
If a template placeholder references an identifier not declared in `inputs:`, it is preserved literally as `${{ inputs.<name> }}` without error.
