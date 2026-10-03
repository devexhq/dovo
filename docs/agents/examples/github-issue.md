# Example GitHub issue

Canonical example of structure, depth, and tone for Dovo CLI issues.
Snapshot aligned with the "Implement config loader" issue shape; edit this file
when the preferred template evolves—do not send agents to the issue tracker for
the exemplar.

The block below is the issue body only (title would be: Implement config loader).

---

## Goal / User Story

**As a** developer using Dovo,
**I want** a reliable loader for `.dovo/config.json`,
**so that** `dovo config show|set|unset|validate` and other commands read the same file with consistent path resolution, typed models, and classified errors—never stack traces or silent misreads.

This issue owns the shared load foundation for the V1 `dovo config` surface described in `docs/cli-plan.md` (milestones 3–6). CLI rendering, mutation, and exit-code wiring are out of scope here.

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
- Document the public load API and error codes in `docs/agents/schemas.md`

### Out of scope
- CLI subcommands (`dovo config show`, `dovo config set`, `dovo config unset`, `dovo config validate`)
- Terminal rendering of effective config or defaults overlay
- Config source metadata header in CLI output
- Mutating config on disk (dot-path set/unset, typed CLI value parsing, atomic writes)
- Standalone schema/validator productization beyond what load requires
- Exit-code policy for `dovo config validate`
- Git-branch / status context warnings and status UX (`load_context` may call the loader; this issue does not redefine status)
- Config generation, repair, or overwrite (`dovo init` generator)

---

## Alignment with `dovo config` (`docs/cli-plan.md`)

| Plan command | Loader responsibility |
|--------------|----------------------|
| `dovo config show` | Read `.dovo/config.json`; classify missing vs invalid |
| `dovo config set` | Load existing config before mutation; surface missing file |
| `dovo config unset` | Load existing config before key removal |
| `dovo config validate` | Same parse + schema path; validate command maps result → exit code later |

Plan behaviors this loader must enable:
- Missing config → structured `not_found` with `dovo init` guidance
- Invalid config → parse and schema errors in `errors[]`
- Schema unknown-key / semantic rules stay with schema and setter work; the loader propagates validation failures and does not invent a second rule set

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
- callers can print `errors` without re-validating

### FR-7: Path collisions and read failures
- Path exists and is a directory → `status` is `path_is_directory`, code `CONFIG_PATH_IS_DIRECTORY`
- Open/read fails (permissions or OS error) → `status` is `unreadable`, code `CONFIG_UNREADABLE`
- Include path and a short Fix hint in `errors`

### FR-8: Typed mapping
On successful schema validation, map into `DovoConfig` covering the full V1 surface:
`version`, `project`, `paths`, `worktree`, `workflow`, `agent`, `patch`, `approval`, `history`, `doctor`, `prune`, `telemetry`.

Normalization:
- `project.name` of `null` maps to `"unnamed_project"`

### FR-9: Primary API is result-oriented
Implement the API in Pre-determined data. `load_config` is the primary surface for commands.

Raising helpers are thin wrappers over the same internals and raise only after a non-`ok` result (for call sites that prefer exceptions). They must not print or exit.

### FR-10: No side effects
The loader must not:
- print to the console
- call `sys.exit`
- create, repair, or overwrite config files

---

## Non-functional requirements

### NFR-1: Single load module
All config reads go through `dovo.core.config.manager` (or one clearly named loader module it owns). Command packages must not call `json.load` on config directly.

### NFR-2: Stable status and error codes
`ConfigLoadStatus` values and `CONFIG_*` code strings in Pre-determined data are stable identifiers for tests. Wording of full user sentences may improve later without renaming codes.

### NFR-3: Shared schema validator
Use packaged `CONFIG_VALIDATOR` / `src/dovo/schemas/v1/config.json`. Do not embed a second schema copy.

### NFR-4: Testable I/O
Filesystem access is via `Path` arguments. Process CWD is only a default for `cwd`, never hard-coded inside helpers.

### NFR-5: Greenfield cutover
Replace superseded load helpers and call sites in the same change. Do not keep dual APIs, shims, or parallel load paths. Tests and docs must match the API in this issue.

---

## Pre-determined data

### Default config path
- Relative: `.dovo/config.json`
- Helper: `get_dovo_config_file(cwd: Path) -> Path` in `dovo.common.fs`

### Status enum

```python
class ConfigLoadStatus(str, Enum):
    OK = "ok"
    NOT_FOUND = "not_found"
    MALFORMED_JSON = "malformed_json"
    ROOT_NOT_OBJECT = "root_not_object"
    SCHEMA_INVALID = "schema_invalid"
    PATH_IS_DIRECTORY = "path_is_directory"
    UNREADABLE = "unreadable"
```

### Error codes

| Code | Status |
|------|--------|
| `CONFIG_NOT_FOUND` | `not_found` |
| `CONFIG_MALFORMED_JSON` | `malformed_json` |
| `CONFIG_ROOT_NOT_OBJECT` | `root_not_object` |
| `CONFIG_SCHEMA_INVALID` | `schema_invalid` |
| `CONFIG_PATH_IS_DIRECTORY` | `path_is_directory` |
| `CONFIG_UNREADABLE` | `unreadable` |

### Result model

```python
class ConfigLoadResult(BaseModel):
    model_config = {"extra": "forbid", "strict": True}

    status: ConfigLoadStatus
    config_path: Path
    raw: dict[str, Any] | None = None
    config: DovoConfig | None = None
    errors: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == ConfigLoadStatus.OK
```

### API

```python
def resolve_config_path(
    cwd: Path | None = None,
    config_path: Path | None = None,
) -> Path:
    """Return absolute path to config.json."""


def load_config(
    cwd: Path | None = None,
    *,
    config_path: Path | None = None,
) -> ConfigLoadResult:
    """Non-raising load + validate. Primary API."""


def load_raw_config(config_path: Path) -> dict[str, Any]:
    """Load JSON object or raise with classified message."""


def parse_and_validate_config(raw: dict[str, Any]) -> DovoConfig:
    """Schema + Pydantic mapping; raise on failure."""


def load_config(
    cwd: Path | None = None,
    *,
    config_path: Path | None = None,
) -> DovoConfig:
    """Return DovoConfig or raise with classified message."""
```

### Schema and defaults ownership
- Schema: `src/dovo/schemas/v1/config.json`
- Canonical defaults (writers only): `CANONICAL_V1_DEFAULTS` in `dovo.core.config.generator`
- The loader never writes defaults to disk

### Implementation locus
Implement in `src/dovo/core/config/manager.py` (refactor existing load helpers to this contract). Delete or rewrite any load path that conflicts with this issue.

---

## CLI output expectations

This issue does not own final `dovo config` chrome. Callers must be able to render the following from `ConfigLoadResult.errors` (and related fields).

### Missing config
```text
Configuration file not found at '/abs/path/.dovo/config.json'.
Fix:
- run `dovo init` to create `.dovo/config.json`
```

### Malformed JSON
```text
Malformed config.json at '/abs/path/.dovo/config.json': <parse detail>
Fix:
- repair JSON syntax, or restore from backup
```

### Schema invalid
```text
Config schema validation failed:
- <error 1>
- <error 2>
Fix:
- run `dovo config validate` for details
- or `dovo init --repair` to insert missing keys without overwriting values
```

---

## Error cases to handle

1. Config file missing → `not_found` + `CONFIG_NOT_FOUND` + init guidance
2. Parent `.dovo/` missing → same as missing file
3. `config.json` path is a directory → `path_is_directory`
4. Permission denied on read → `unreadable`
5. Empty file / truncated JSON → `malformed_json`
6. JSON root array or scalar → `root_not_object`
7. Object missing required V1 keys → `schema_invalid` with per-error list
8. Wrong field types (e.g. `worktree.max_active_worktrees` as string) → `schema_invalid`
9. `version` ≠ 1 → `schema_invalid`

---

## Definition of done

- After `dovo init`, `load_config` succeeds on the generated file
- Before init, `load_config` reports `not_found`
- Tests cover every `ConfigLoadStatus` value
- In-tree callers use the new load API; superseded dual load paths are removed
- `docs/agents/schemas.md` documents the load API and error codes
- No `dovo config` CLI subcommand is added in this change
