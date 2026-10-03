# Assertions Schema Reference

This reference documents the YAML specification for step-level quality gates and verification criteria under the `assert:` block.

---

## `StepAssert` Fields

| Key | Type | Default | Description |
|---|---|---|---|
| `exit_code` | `integer \| list[integer]` | `0` | Expected exit code(s). If `assert` is present but `exit_code` is omitted, defaults to expecting `0`. |
| `output_contains` | `string \| list[string]` | `null` | Substring(s) that must appear in the combined stdout/stderr output. |
| `output_not_contains` | `string \| list[string]` | `null` | Substring(s) that must NOT appear in output. |
| `regex_match` | `string` | `null` | Regular expression pattern that must match within the combined output. |
| `json_match` | `JSONMatchObject` | `null` | Dot-path assertion evaluated against parsed JSON on `stdout`. |
| `file_exists` | `string \| list[string]` | `null` | Relative path(s) in the execution root that must exist as files. |
| `file_not_exists` | `string \| list[string]` | `null` | Relative path(s) in the execution root that must NOT exist. |
| `file_not_empty` | `string \| list[string]` | `null` | Relative path(s) in the execution root that must exist and have non-zero size. |

---

## File Path Validation Rules

All file assertions (`file_exists`, `file_not_exists`, `file_not_empty`) resolve against the execution root: normally the worktree, or the working tree when `dovo run --no-worktree` is used. They must adhere to these safety constraints:
* Must be non-empty strings.
* Must be relative paths inside the execution root (absolute paths starting with `/` or drive letters like `C:/` are rejected).
* Parent directory traversal (`..`) is strictly prohibited.

---

## `json_match` Structure

The `json_match` object allows verifying structured JSON output printed to `stdout`:

```yaml
assert:
  json_match:
    path: "summary.status"
    operator: "eq"
    value: "APPROVED"
```

| Field | Type | Description |
|---|---|---|
| `path` | `string` | Dot-delimited JSON property path (e.g. `report.totals.failed`). |
| `operator` | `string` | Comparison operator: `eq`, `neq`, `contains`, `gt`, `gte`, `lt`, `lte`. |
| `value` | `any` | Target comparison value. |

---

## Full Assertion Example

```yaml
- id: test-and-report
  name: Execute test suite with assertions
  run: pytest --json-report --json-report-file=report.json
  assert:
    exit_code: [0, 1]
    output_contains: "0 errors"
    output_not_contains: ["FATAL", "PANIC"]
    regex_match: "([0-9]+) passed"
    json_match:
      path: "summary.passed"
      operator: "gt"
      value: 10
    file_exists: "report.json"
    file_not_empty: "report.json"
    file_not_exists: "tmp/lock"
```
