# `dovo config`

The `dovo config` command inspects, updates, and validates the local `.dovo/config.json` configuration file.

## Subcommands

### `dovo config show`

Displays the effective configuration loaded from the four configuration tiers (see below) formatted as JSON:

```bash
dovo config show
```

#### Configuration Precedence

The displayed configuration is the merge of four tiers, in increasing precedence: Packaged defaults, Global (`$DOVO_HOME/global/config.json`), User (`$DOVO_HOME/user/config.json`), and Repo (`.dovo/config.json`). `DOVO_HOME` defaults to `~/.dovo` when unset. A field set by a higher-precedence tier overrides the same field from a lower one; fields left unset by every tier fall back to the packaged default. A missing Repo tier is not backfilled — `dovo config show` still fails with `CONFIG_NOT_FOUND`, directing you to `dovo init`. A malformed or schema-invalid Global or User tier file produces a "Config Error" panel naming the offending tier and file path rather than a silent fallback. In `--format json`, the envelope's `raw` field now carries this fully-merged effective payload (every `DovoConfig` field, defaults included) rather than the literal contents of `.dovo/config.json` alone.

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
# Change LLM provider to Ollama
dovo config set agent.provider ollama

# Set model name
dovo config set agent.model llama3.1

# Configure the worktree base ref
dovo config set worktree.base_ref main
```

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

If validation fails, `dovo config validate` prints detailed error descriptions highlighting missing required fields or invalid property types. An `agent.provider` that the schema accepts but no adapter implements (`openai`, `anthropic`, `azure_openai`, `custom`) is reported as an error carrying `AGENT_PROVIDER_UNSUPPORTED`.
