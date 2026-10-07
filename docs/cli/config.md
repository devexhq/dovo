# `dovo config`

The `dovo config` command inspects, updates, and validates the local `.dovo/config.json` configuration file.

Every `dovo config` subcommand requires `dovo init` first: without a valid `.dovo/project.json` it exits `1` with `Workspace is not initialized` and a `dovo init` prompt. Once initialized, the subcommands still run when `config.json` is missing or broken so they can report on it.

## Subcommands

### `dovo config show`

Displays the effective configuration loaded from the four configuration tiers (see below) formatted as JSON:

```bash
dovo config show
```

#### Configuration Precedence

The displayed configuration is the merge of four tiers, in increasing precedence: Packaged defaults, Global (`$DOVO_HOME/global/config.json`), User (`$DOVO_HOME/user/config.json`), and Repo (`.dovo/config.json`). `DOVO_HOME` defaults to `~/.dovo` when unset. A field set by a higher-precedence tier overrides the same field from a lower one; fields left unset by every tier fall back to the packaged default. A missing Repo tier is not backfilled — `dovo config show` still fails with `CONFIG_NOT_FOUND`, directing you to `dovo init`. The one exception is `environment.sensitive_variables`: names from the Global, User and Repo tiers are unioned in precedence order (duplicates dropped) instead of the higher tier replacing the list. A malformed or schema-invalid Global or User tier file produces a "Config Error" panel naming the offending tier and file path rather than a silent fallback. In `--format json`, the envelope's `raw` field now carries this fully-merged effective payload (every `DovoConfig` field, defaults included) rather than the literal contents of `.dovo/config.json` alone.

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

Lists environment variable names whose values Dovo masks, with no minimum length, as `[REDACTED:<NAME>]` in step output, `diff.patch`, `dovo logs` and `dovo history`. The config stores names only; values are read from the process environment, from a step's own `env:`, or from the repository `.env` at masking time. A listed name that is unset or blank is skipped silently.

```bash
dovo config set environment.sensitive_variables '["AWS_ACCESS_KEY_ID","DATABASE_URL"]'
```

Each entry must match `^[A-Za-z_][A-Za-z0-9_]*$`; any other entry (for example `API-KEY` or `TOKEN=abc`) fails validation with an error naming `environment.sensitive_variables[<index>]`, and the error never echoes the entry. Unknown keys under `environment` are rejected.

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

Validates `.dovo/config.json` against the Dovo V1 JSON Schema and semantic rules:

```bash
dovo config validate
```

If validation fails, `dovo config validate` prints detailed error descriptions highlighting missing required fields or invalid property types. An `agent.provider` outside the supported set (only `copilot`) is reported as a structural schema error (`CONFIG_SCHEMA_INVALID`), and the config file is not modified; choose an installed, registered provider. The `AGENT_PROVIDER_UNSUPPORTED` error cannot occur while the schema and the provider registry agree.
