# `dovo init`

`dovo init` sets up a repository for Dovo: the `.dovo/` directory with a project identity, default configuration, a local `.gitignore` and the catalog folders.

## Usage

```bash
dovo init [OPTIONS]
```

## Options

| Flag | Description |
| --- | --- |
| `--id <ID>` | Explicit unique project ID slug. Must match `^[a-z0-9][a-z0-9-_]{2,62}$`; an invalid value exits with code `2` and writes nothing. When omitted, a slug is generated. |
| `--display-name <NAME>` | Human-readable project display name. |
| `--force` | Overwrite an existing `project.json`'s id when `--id` is also provided. Has no effect on its own, and never touches `config.json`. |
| `--format <terminal\|json>` | Presentation format (`terminal` or `json`). Defaults to `terminal`. |
| `--repair` | Add missing required config keys without overwriting user values. |
| `--overwrite` | Replace an existing config with fresh defaults (destructive). |

### `--id` / `--display-name` / `--force`

`dovo init` writes `<repo>/.dovo/project.json` containing a stable project identity (`id`, `display_name`, `created_at`). Without `--id`, a generated slug (e.g. `brave-otter`) is used. Rerunning `dovo init` preserves an existing identity unless both `--id` and `--force` are passed together, in which case the identity is overwritten with the new `--id`. `--force` alone, without `--id`, has no effect on an existing identity or on `config.json`.

```bash
dovo init --id my-project --display-name "My Project"
dovo init --id my-project --force   # only effective on a rerun that changes the id
```

### `.dovo/.gitignore`

`dovo init` creates `<repo>/.dovo/.gitignore` with the ignore rules for `.dovo/`. When the file already exists, `dovo init` appends any of those rules it is missing and keeps every existing line. The repository-root `.gitignore` is never modified.

### `--repair`

Adds any missing keys to `.dovo/config.json` with default values and keeps your existing settings.

```bash
dovo init --repair
```

### `--overwrite`

Replaces `.dovo/config.json` with fresh defaults. The project identity in `project.json` is unchanged.

```bash
dovo init --overwrite
```

### `--format`

With `json`, emits structured NDJSON for scripts and tools.

```bash
dovo init --format json
```

## Error codes

Every failure reports its cause as `error_code` in `--format json` output; on success `error_code` is `null`. An invalid `--id` exits with code `2`; every other failure exits with code `1`.

| Code | Cause |
| --- | --- |
| `BOOTSTRAP_TARGET_FORBIDDEN` | The directory's `.dovo` is the global Dovo home (`Cannot initialize a workspace at '<root>': its .dovo directory is the global Dovo home '<DOVO_HOME>'.`). Run `dovo init` inside a project directory, or set `DOVO_HOME` to a different directory. Checked first, before anything is written. |
| `BOOTSTRAP_REPO_NOT_FOUND` | The current directory is not a Git repository (`The current directory is not a valid Git repository.`). Run `git init` first. Checked before anything is written. |
| `PROJECT_ID_INVALID` | `--id` does not match `^[a-z0-9][a-z0-9-_]{2,62}$`. Exits `2` and writes nothing. |
| `BOOTSTRAP_PATH_INVALID` | A `.dovo/` directory is a file, a symlink, or cannot be created. |
| `BOOTSTRAP_PATH_UNWRITABLE` | A `.dovo/` directory does not exist after creation or is not writable. |
| `BOOTSTRAP_METADATA_WRITE_FAILED` | The bootstrap metadata under `.dovo/.meta` cannot be written. |
| `PROJECT_IDENTITY_UNREADABLE` | An existing `.dovo/project.json` cannot be read. |
| `PROJECT_IDENTITY_SCHEMA_INVALID` | An existing `.dovo/project.json` is not a valid project identity. |
| `PROJECT_IDENTITY_WRITE_FAILED` | `.dovo/project.json` cannot be written. |
| `CONFIG_PATH_INVALID` | `.dovo/config.json` is a directory. |
| `CONFIG_JSON_INVALID` | `--repair` found a `.dovo/config.json` that is not valid JSON. |
| `CONFIG_ROOT_INVALID` | `--repair` found a `.dovo/config.json` whose root is not a JSON object. |
| `CONFIG_SCHEMA_INVALID` | The configuration to write fails schema validation, for example a repaired file with an invalid value. |
| `CONFIG_WRITE_FAILED` | `.dovo/config.json` cannot be written. |
| `CATALOG_SEED_FAILED` | A starter catalog file cannot be written. |
| `WORKSPACE_LOCK_TIMEOUT` | Another `dovo` process held the workspace lock until the wait timed out. Reported as an [`error` event](../reference/json-output.md#the-error-event). Wait for the other process to finish, then rerun `dovo init`. |
| `INTERNAL_ERROR` | An unexpected error in any step of `dovo init`. |

The global-home refusal applies to a directory whose `.dovo` is `DOVO_HOME` (by default your home directory, whose `.dovo` is `~/.dovo`), even when it is a Git repository.

When `dovo init` fails with an unexpected error in any of its steps (setting up the `.dovo/` directory, the project identity, the database, the configuration or the starter blueprints), or the operating system refuses (for example, with a permission error) to create a `.dovo/` directory, write its bootstrap metadata, read or write `.dovo/project.json`, write `.dovo/config.json` or write a seeded catalog file, Dovo writes a [diagnostic log](doctor.md#diagnostic-logs) record. The error message is unchanged, a `Details logged: <path> (event <event_id>)` line naming that file follows it, and `--format json` lists the record in `diagnostics`; if the record cannot be written, there is no line and `diagnostics` is empty. An unexpected error reports `error_code` `INTERNAL_ERROR`; an operating-system error reports the `error_code` of the step that failed.

## Exit codes

- `0`: the workspace is initialized, including a rerun that finds it already initialized.
- `2`: an invalid `--id` (`PROJECT_ID_INVALID`), or a usage error such as an unknown option.
- `1`: every other failure, including a workspace lock timeout and an unexpected error.

See [JSON Output](../reference/json-output.md#exit-codes) for the rule every command follows.

## Examples

### Initializing a new repository

```bash
cd /path/to/my-repo
dovo init
```

### Initializing with an explicit project id

```bash
dovo init --id my-project --display-name "My Project"
```

### Repairing schema drift after updating `dovo`

```bash
dovo init --repair
```

### JSON structured output

```bash
dovo init --id my-project --format json
```

Emits a JSON payload:

```json
{"version": 1, "event_type": "init", "payload": {"ok": true, "error_code": null, "errors": [], "warnings": [], "remediations": [], "diagnostics": [], "root_path": "/path/to/my-repo/.dovo", "root_path_relative": ".dovo", "bootstrap_outcome": "initialized", "dirs_created": [".dovo/.meta"], "project_id": "my-project", "identity_path_relative": ".dovo/project.json", "identity_preserved": false, "gitignore_path_relative": ".dovo/.gitignore", "gitignore_tracked_entries": ["config.json", "project.json", "catalog/"], "config_created": true, "config_overwritten": false, "config_repaired": false, "config_skipped_existing": false, "config_path_relative": ".dovo/config.json", "inserted_keys": [], "seeded_files": [".dovo/catalog/blueprints/dovo/fix-tests.yml", ".dovo/catalog/blueprints/dovo/review-fix.yml", ".dovo/catalog/steps/dovo/ai-code-patcher.yml", ".dovo/catalog/steps/dovo/ai-planner.yml", ".dovo/catalog/steps/dovo/ai-reviewer.yml", ".dovo/catalog/steps/dovo/git-sync-base.yml", ".dovo/catalog/steps/dovo/run-tests.yml"], "skipped_seed_files": [], "overwritten_seed_files": []}}
```
