# Example GitHub issue

Canonical example of structure, depth, and tone for Dovo CLI issues.
The block below is the issue body only (title: `Implement config loader`).

---

## Goal / User Story

**As a** developer using Dovo,
**I want** a reliable loader for `.dovo/config.json`,
**so that** `dovo config show|set|unset|validate` and other commands read the same file with consistent path resolution, typed models, and classified errors—never stack traces or silent misreads.

---

## Scope

### In scope
- Resolve the default config path to `<repo>/.dovo/config.json` via `get_dovo_config_file`
- Load raw JSON from disk with stable, classified failures
- Validate against config schema v1 and map into typed `DovoConfig`
- Expose a non-raising `ConfigLoadResult` API as the primary load surface
- Distinct load statuses for: missing file, malformed JSON, wrong root type, schema validation failure, path-is-directory, unreadable path
- Stable error code strings suitable for tests and doctor checks
- No console output, no `sys.exit`, no file creation or mutation inside the loader
- Unit tests for the success path and every load status
- Update in-tree callers and tests to the new API in the same change

### Out of scope
- CLI subcommands (`dovo config show`, `dovo config set`, `dovo config unset`, `dovo config validate`)
- Terminal rendering of effective config or defaults overlay
- Mutating config on disk (dot-path set/unset, typed CLI value parsing, atomic writes)
- Config generation, repair, or overwrite (`dovo init` generator)

---

## Functional requirements

### FR-1: Default path resolution
Given repository root `cwd`, or an explicit `config_path`:
- default path is `get_dovo_config_file(cwd)` → `cwd / ".dovo" / "config.json"`
- explicit `config_path` wins when provided
- resolved path on the result is absolute

### FR-2: Success path
When the file exists, is a regular file, and contains a JSON object that passes config schema v1:
- `status` is `ok`
- `raw` is the parsed `dict[str, Any]`
- `config` is a populated `DovoConfig`
- `errors` is empty
- the file is not modified

### FR-3: Missing config
When the file does not exist (including missing parent `.dovo/`):
- `status` is `not_found`
- `errors` includes `CONFIG_NOT_FOUND` guidance that names the resolved path and tells the user to run `dovo init`

### FR-4: Malformed JSON
When the file exists but is not valid JSON:
- `status` is `malformed_json`
- `errors` includes `CONFIG_MALFORMED_JSON` with path and parse detail (line/col when available)

### FR-5: Wrong root type
When JSON parses but the root is not an object:
- `status` is `root_not_object`
- `errors` includes `CONFIG_ROOT_NOT_OBJECT`
- schema validation is not run on non-objects

### FR-6: Schema validation failure
When the root is an object that fails config schema v1 or Pydantic mapping:
- `status` is `schema_invalid`
- `errors` is a non-empty list of human-readable messages (jsonschema paths/messages)

### FR-7: Path collisions and read failures
- Path exists and is a directory → `status` is `path_is_directory`, code `CONFIG_PATH_IS_DIRECTORY`
- Open/read fails (permissions or OS error) → `status` is `unreadable`, code `CONFIG_UNREADABLE`
- Include path and a short Fix hint in `errors`

---

## Non-functional requirements

### NFR-1: Single load module
All config reads go through `dovo.core.config.manager`. Command packages must not call `json.load` on config directly.

### NFR-2: Testable I/O
Filesystem access is via `Path` arguments. Process CWD is only a default for `cwd`, never hard-coded inside helpers.

### NFR-3: Greenfield cutover
Replace superseded load helpers and call sites in the same change. Do not keep dual APIs, shims, or parallel load paths.

---

## Pre-determined data

### Status enum

```python
class ConfigLoadStatus(StrEnum):
    OK = "ok"
    NOT_FOUND = "not_found"
    MALFORMED_JSON = "malformed_json"
    ROOT_NOT_OBJECT = "root_not_object"
    SCHEMA_INVALID = "schema_invalid"
    PATH_IS_DIRECTORY = "path_is_directory"
    UNREADABLE = "unreadable"
```

### Result model

```python
class ConfigLoadResult(BaseResult):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: ConfigLoadStatus
    config_path: Path
    raw: dict[str, Any] | None = None
    config: DovoConfig | None = None

    @property
    def ok(self) -> bool:
        return self.status == ConfigLoadStatus.OK
```

---

## CLI output expectations

### Missing config
```text
Configuration file not found at '/abs/path/.dovo/config.json'.
Fix:
- run `dovo init` to create `.dovo/config.json`
```

---

## Error cases to handle

1. Config file missing or parent `.dovo/` missing → `not_found` + `CONFIG_NOT_FOUND` + init guidance
2. `config.json` path is a directory → `path_is_directory`
3. Permission denied on read → `unreadable`
4. Empty file / truncated JSON → `malformed_json`
5. JSON root array or scalar → `root_not_object`
6. Object missing required V1 keys or wrong field types → `schema_invalid` with per-error list

---

## Definition of done

- After `dovo init`, `load_config` succeeds on the generated file
- Tests cover every `ConfigLoadStatus` value
- In-tree callers use the new load API; superseded dual load paths are removed
- No `dovo config` CLI subcommand is added in this change
