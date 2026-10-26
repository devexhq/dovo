# `dovo init`

`dovo init` sets up a repository for Dovo: the `.dovo/` directory with a project identity, default configuration, a local `.gitignore` and the catalog folders.

## Usage

```bash
dovo init [OPTIONS]
```

## Options

| Flag | Description |
| --- | --- |
| `--id <ID>` | Explicit unique project ID slug. Must match `^[a-z0-9][a-z0-9-_]{2,62}$`; an invalid value exits with code `2`. When omitted, a slug is generated. |
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

## Errors

Both errors exit with code `1` and report `failure_mode: "preflight"` in JSON output. Nothing is written.

| Error | Fix |
| --- | --- |
| `The current directory is not a valid Git repository.` | Run `git init` before running `dovo init`. |
| `Cannot initialize a workspace at '<root>': its .dovo directory is the global Dovo home '<DOVO_HOME>'.` | Run `dovo init` inside a project directory, or set `DOVO_HOME` to a different directory. |

The global-home refusal applies to a directory whose `.dovo` is `DOVO_HOME` (by default your home directory, whose `.dovo` is `~/.dovo`), even when it is a Git repository, and is checked before the Git check.

When `dovo init` fails with an unexpected error while setting up the project identity, or the operating system refuses to read or write `.dovo/project.json` (for example, a permission error), Dovo writes a [diagnostic log](doctor.md#diagnostic-logs) record. The error message is unchanged, a `Details logged: <path> (event <event_id>)` line naming that file follows it, and `--format json` lists the record in `diagnostics`; if the record cannot be written, there is no line and `diagnostics` is empty. These failures exit with code `1` and report `failure_mode: "bootstrap"`.

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
{"event_type": "WorkspaceInitResult", "payload": {"ok": true, "root_path": "/path/to/my-repo/.dovo", "root_path_relative": ".dovo", "bootstrap_outcome": "initialized", "dirs_created": [".dovo/.meta"], "project_id": "my-project", "identity_path_relative": ".dovo/project.json", "identity_preserved": false, "gitignore_path_relative": ".dovo/.gitignore", "gitignore_tracked_entries": ["config.json", "project.json", "catalog/"], "config_created": true, "config_overwritten": false, "config_repaired": false, "config_skipped_existing": false, "config_path_relative": ".dovo/config.json", "inserted_keys": [], "seeded_files": [".dovo/catalog/blueprints/dovo/fix-tests.yml", ".dovo/catalog/blueprints/dovo/review-fix.yml", ".dovo/catalog/steps/dovo/ai-code-patcher.yml", ".dovo/catalog/steps/dovo/ai-planner.yml", ".dovo/catalog/steps/dovo/ai-reviewer.yml", ".dovo/catalog/steps/dovo/git-sync-base.yml", ".dovo/catalog/steps/dovo/run-tests.yml"], "skipped_seed_files": [], "overwritten_seed_files": [], "failure_mode": null, "errors": [], "warnings": [], "remediations": [], "diagnostics": []}}
```
