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
- Resolve the default config path to `<repo>/.dovo/config.json`
- Load raw JSON from disk with stable, classified failures
- Validate against config schema v1 and map into typed `DovoConfig`
- Expose a non-raising `Config.load` entrypoint returning `ConfigLoadResult` as the primary load surface; helpers behind it raise `ConfigError`, the package's `DomainError`, and `Config.load` catches it once with `catch_failure`
- Distinct error codes for: missing file, malformed JSON, wrong root type, schema validation failure, path-is-directory, unreadable path
- Stable error code strings suitable for tests and doctor checks
- No console output, no `sys.exit`, no file creation or mutation inside the loader
- Unit tests for the success path and every load error code
- Update in-tree callers and tests to the new API in the same change

### Owned elsewhere
- #412 owns the CLI subcommands (`dovo config show`, `dovo config set`, `dovo config unset`, `dovo config validate`) and their terminal rendering of effective config and the defaults overlay
- #413 owns mutating config on disk (dot-path set/unset, typed CLI value parsing, atomic writes)
- #414 owns config generation, repair and overwrite (the `dovo init` generator)

---

## Functional requirements

### FR-1: Default path resolution
Given repository root `cwd`, or an explicit `config_path`:
- default path is `cwd / ".dovo" / "config.json"`
- explicit `config_path` wins when provided
- resolved path on the result is absolute

### FR-2: Success path
When the file exists, is a regular file, and contains a JSON object that passes config schema v1:
- `ok` is true and `error_code` is `None`
- `raw` is the parsed `dict[str, Any]`
- `config` is a populated `DovoConfig`
- `errors` is empty
- the file is not modified

### FR-3: Missing config
When the file does not exist (including missing parent `.dovo/`):
- `error_code` is `CONFIG_NOT_FOUND`
- `errors` names the resolved path and tells the user to run `dovo init`

### FR-4: Malformed JSON
When the file exists but is not valid JSON:
- `error_code` is `CONFIG_JSON_INVALID`
- `errors` carries the path and parse detail (line/col when available)

### FR-5: Wrong root type
When JSON parses but the root is not an object:
- `error_code` is `CONFIG_ROOT_INVALID`
- `errors` names the path
- schema validation is not run on non-objects

### FR-6: Schema validation failure
When the root is an object that fails config schema v1 or Pydantic mapping:
- `error_code` is `CONFIG_SCHEMA_INVALID`
- `errors` is a non-empty list of human-readable messages (jsonschema paths/messages)

### FR-7: Path collisions and read failures
- Path exists and is a directory → `error_code` is `CONFIG_PATH_INVALID`
- Open/read fails (permissions or OS error) → `error_code` is `CONFIG_UNREADABLE`
- Include path and a short Fix hint in `errors`

---

## Non-functional requirements

### NFR-1: Single load module
Every config read goes through the loader's entrypoint. Command packages must not call `json.load` on config directly.

### NFR-2: Testable I/O
Filesystem access is via `Path` arguments. Process CWD is only a default for `cwd`, never hard-coded inside helpers.

### NFR-3: Greenfield cutover
Replace superseded load helpers and call sites in the same change. Do not keep dual APIs, shims, or parallel load paths.

---

## Pre-determined data

### Domain error

In `core/config/exceptions.py`:

```python
class ConfigError(DomainError):
    """The config package's one business-failure exception."""
```

### Result model

```python
class ConfigLoadResult(BaseResult):
    model_config = ConfigDict(extra="forbid", strict=True)

    config_path: Path
    raw: dict[str, Any] | None = None
    config: DovoConfig | None = None

    @property
    def ok(self) -> bool:
        return self.error_code is None
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

1. Config file missing or parent `.dovo/` missing → `CONFIG_NOT_FOUND` + init guidance
2. `config.json` path is a directory → `CONFIG_PATH_INVALID`
3. Permission denied on read → `CONFIG_UNREADABLE`
4. Empty file / truncated JSON → `CONFIG_JSON_INVALID`
5. JSON root array or scalar → `CONFIG_ROOT_INVALID`
6. Object missing required V1 keys or wrong field types → `CONFIG_SCHEMA_INVALID` with per-error list

---

## Definition of done

- After `dovo init`, `Config.load` returns an ok result on the generated file
- Zero references to superseded config load helpers remain in `src/` or `tests/`
