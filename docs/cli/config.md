# `dovo config`

`dovo config` shows, changes and validates configuration.

Every `dovo config` subcommand requires `dovo init` first: without a valid `.dovo/project.json` it exits `1` with `Workspace is not initialized` and a `dovo init` prompt. Once initialized, the subcommands still run when `config.json` is missing or broken so they can report on it.

## Subcommands

### `dovo config show`

Prints the effective configuration, merged from four tiers (see below), as JSON:

```bash
dovo config show
```

#### Configuration Precedence

Tiers, lowest to highest precedence: Packaged defaults, Global (`$DOVO_HOME/global/config.json`), User (`$DOVO_HOME/user/config.json`), and Repo (`.dovo/config.json`). `DOVO_HOME` defaults to `~/.dovo` when unset. A field set by a higher tier overrides the same field from a lower one; fields no tier sets use the packaged default.

- If the Repo tier is missing, `dovo config show` exits with code 1, prints a "Config Error" panel reading `Configuration file not found at '<path>'.` and points you to `dovo init`. With `--format json`, `status` is `not_found` and `error_code` is `CONFIG_NOT_FOUND`.
- A broken Repo tier exits with code 1 and reports its `error_code` with `--format json`: `CONFIG_PATH_INVALID` when `.dovo/config.json` is a directory, `CONFIG_UNREADABLE` when it cannot be read, `CONFIG_JSON_INVALID` when it is not valid JSON, `CONFIG_ROOT_INVALID` when its root is not an object, and `CONFIG_SCHEMA_INVALID` when it fails schema validation. A successful run reports `error_code` `null`.
- When `.dovo/config.json` cannot be read, Dovo writes a [diagnostic log](doctor.md#diagnostic-logs) record, prints a `Details logged: <path> (event <event_id>)` line after the panel, and lists the record in `diagnostics` with `--format json`. If the record cannot be written, the line is absent.
- `environment.sensitive_variables` is the exception: names from the Global, User and Repo tiers are combined, with duplicates dropped.
- A malformed or invalid Global or User file produces a "Config Error" panel naming the tier and file path.
- With `--format json`, `raw` contains the full merged configuration, defaults included.

### `dovo config set`

Sets a configuration value using a dot-path key selector:

```bash
dovo config set <key> <value>
```

#### Arguments

- `key`: Key or nested dot-path (e.g. `default_agent`, `agents.copilot.model`, `worktree.base_ref`). Missing intermediate objects such as `agents` and `agents.copilot` are created.
- `value`: New value to store, coerced by the rules below.

#### Value coercion

`dovo config set` coerces `value` once, applying the first rule that matches:

1. Wrapped in double quotes (`'"12345"'`, `'"true"'`, `'""'`): the text between the quotes, stored as a string with no further coercion.
2. `true` or `yes` → `true`; `false` or `no` → `false` (case-insensitive).
3. An integer, as Python's `int()` reads it (`42`, `-3`, `' 12 '`, `1_000`) → integer. Pass `--` before the key for a negative number (`dovo config set -- <key> -3`), otherwise `-3` is read as an option.
4. A float, as Python's `float()` reads it (`1.5`, `1e3`) → number.
5. A JSON array or object (`'["A","B"]'`, `'{"a": 1}'`) → that array or object.
6. Anything else, including `null` and `gpt-4.1`, is stored as a string.

Quote a value in double quotes, inside shell single quotes, to store a string that would otherwise be coerced.

#### Examples

```bash
# Select the default agent provider
dovo config set default_agent copilot

# Set the model for one provider
dovo config set agents.copilot.model gpt-4.1

# Configure the worktree base ref
dovo config set worktree.base_ref main

# Store a numeric-looking ref as a string
dovo config set worktree.base_ref '"12345"'
```

On failure, `dovo config set` exits with code 1, leaves `.dovo/config.json` unchanged and reports an `error_code` with `--format json`: the code [`dovo config show`](#dovo-config-show) reports when `.dovo/config.json` is missing or broken, `CONFIG_SCHEMA_INVALID` when the new value fails schema validation, `CONFIG_TYPE_CONFLICT` when the key crosses a value that is not an object, `CONFIG_KEY_PATH_INVALID` when the key is empty or has an empty segment, and `CONFIG_WRITE_FAILED` when the file cannot be written. When the file cannot be read or written, Dovo writes a [diagnostic log](doctor.md#diagnostic-logs) record, prints a `Details logged: <path> (event <event_id>)` line after the panel, and lists the record in `diagnostics` with `--format json`. A successful run reports `error_code` `null`.

#### `environment.sensitive_variables`

Names of environment variables whose values Dovo masks as `[REDACTED:<NAME>]` in step output, `diff.patch`, `dovo session` commands, with no minimum length. The config stores names only; values are read at masking time from the process environment, a step's `env:` or the repository `.env`. A listed name that is unset or blank is ignored.

```bash
dovo config set environment.sensitive_variables '["AWS_ACCESS_KEY_ID","DATABASE_URL"]'
```

Each entry must match `^[A-Za-z_][A-Za-z0-9_]*$`. Any other entry (for example `API-KEY` or `TOKEN=abc`) fails validation with an error naming `environment.sensitive_variables[<index>]`; the entry itself is not echoed. Unknown keys under `environment` are rejected.

### `dovo config unset`

Removes a configuration value at a dot-path key, falling back to its schema default on next load:

```bash
dovo config unset <key>
```

#### Arguments

- `key`: Key or nested dot-path (e.g. `agents.copilot.model`, `telemetry.enabled`).

#### Examples

```bash
# Remove a provider's model, falling back to the schema default
dovo config unset agents.copilot.model

# Remove an entire section, falling back to its section defaults
dovo config unset agents
```

Removing a key that is already absent is a no-op and exits successfully without writing to disk. Removing the last key of a provider block leaves an empty block (`"copilot": {}`) under `agents`.

On failure, `dovo config unset` exits with code 1, leaves `.dovo/config.json` unchanged and reports the same `error_code`, `Details logged:` line and `diagnostics` entry as [`dovo config set`](#dovo-config-set), with `CONFIG_SCHEMA_INVALID` when the file would fail schema validation without the key.

### `dovo config validate`

Checks `.dovo/config.json` against the schema:

```bash
dovo config validate
```

On failure it lists the missing fields or invalid values. A `default_agent` value or `agents` key other than `copilot` or `claude`, and the legacy top-level `agent` key, are reported as schema validation errors, and the command exits with code 1; with `--format json`, `status` is `invalid` and `errors` lists them. The file is never modified.

When `.dovo/config.json` cannot be loaded, `dovo config validate` exits with code 1 and reports the same `error_code`, `errors` and remediations as [`dovo config show`](#dovo-config-show), including the `Details logged:` line and `diagnostics` entry when the file cannot be read; a missing file reports `status` `not_found` and `error_code` `CONFIG_NOT_FOUND`. A file that fails schema validation reports `status` `invalid` and `error_code` `CONFIG_SCHEMA_INVALID`. A valid file reports `error_code` `null`.
