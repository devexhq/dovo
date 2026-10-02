# `wt config`

The `wt config` command inspects, updates, and validates the local `.worktree/config.json` configuration file.

## Subcommands

### `wt config show`

Displays the effective configuration loaded from the four configuration tiers (see below) formatted as JSON:

```bash
wt config show
```

#### Configuration Precedence

The displayed configuration is the merge of four tiers, in increasing precedence: Packaged defaults, Global (`$WORKTREE_HOME/global/config.json`), User (`$WORKTREE_HOME/user/config.json`), and Repo (`.worktree/config.json`). `WORKTREE_HOME` defaults to `~/.worktree` when unset. A field set by a higher-precedence tier overrides the same field from a lower one; fields left unset by every tier fall back to the packaged default. A missing Repo tier is not backfilled — `wt config show` still fails with `CONFIG_NOT_FOUND`, directing you to `wt init`. A malformed or schema-invalid Global or User tier file produces a "Config Error" panel naming the offending tier and file path rather than a silent fallback. In `--format json`, the envelope's `raw` field now carries this fully-merged effective payload (every `WorktreeConfig` field, defaults included) rather than the literal contents of `.worktree/config.json` alone.

### `wt config set`

Sets a configuration value using a dot-path key selector:

```bash
wt config set <key> <value>
```

#### Arguments

- `key`: Key or nested dot-path (e.g. `agent.provider`, `agent.model`, `sandbox.base_ref`).
- `value`: New value to store.

#### Examples

```bash
# Change LLM provider to Ollama
wt config set agent.provider ollama

# Set model name
wt config set agent.model llama3.1

# Configure the sandbox base ref
wt config set sandbox.base_ref main
```

### `wt config unset`

Removes a configuration value at a dot-path key, falling back to its schema default on next load:

```bash
wt config unset <key>
```

#### Arguments

- `key`: Key or nested dot-path (e.g. `agent.model`, `telemetry.enabled`).

#### Examples

```bash
# Remove a custom model override, falling back to the schema default
wt config unset agent.model

# Remove an entire section, falling back to its section defaults
wt config unset agent
```

Removing a key that is already absent is a no-op and exits successfully without writing to disk.

### `wt config validate`

Validates `.worktree/config.json` against the Worktree V1 JSON Schema and semantic rules:

```bash
wt config validate
```

If validation fails, `wt config validate` prints detailed error descriptions highlighting missing required fields or invalid property types. An `agent.provider` that the schema accepts but no adapter implements (`openai`, `anthropic`, `azure_openai`, `custom`) is reported as an error carrying `AGENT_PROVIDER_UNSUPPORTED`.
