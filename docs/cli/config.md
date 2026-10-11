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

- If the Repo tier is missing, `dovo config show` fails with `CONFIG_NOT_FOUND` and points you to `dovo init`.
- `environment.sensitive_variables` is the exception: names from the Global, User and Repo tiers are combined, with duplicates dropped.
- A malformed or invalid Global or User file produces a "Config Error" panel naming the tier and file path.
- With `--format json`, `raw` contains the full merged configuration, defaults included.

### `dovo config set`

Sets a configuration value using a dot-path key selector:

```bash
dovo config set <key> <value>
```

#### Arguments

- `key`: Key or nested dot-path (e.g. `agent.provider`, `agent.model`, `worktree.base_ref`).
- `value`: New value to store.

#### Examples

```bash
# Select the agent provider
dovo config set agent.provider copilot

# Set model name
dovo config set agent.model gpt-4.1

# Configure the worktree base ref
dovo config set worktree.base_ref main
```

#### `environment.sensitive_variables`

Names of environment variables whose values Dovo masks as `[REDACTED:<NAME>]` in step output, `diff.patch`, `dovo logs` and `dovo history`, with no minimum length. The config stores names only; values are read at masking time from the process environment, a step's `env:` or the repository `.env`. A listed name that is unset or blank is ignored.

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

- `key`: Key or nested dot-path (e.g. `agent.model`, `telemetry.enabled`).

#### Examples

```bash
# Remove a custom model override, falling back to the schema default
dovo config unset agent.model

# Remove an entire section, falling back to its section defaults
dovo config unset agent
```

Removing a key that is already absent is a no-op and exits successfully without writing to disk.

### `dovo config validate`

Checks `.dovo/config.json` against the schema:

```bash
dovo config validate
```

On failure it lists the missing fields or invalid values. An `agent.provider` other than `copilot` is reported as `CONFIG_SCHEMA_INVALID`. The file is never modified.
