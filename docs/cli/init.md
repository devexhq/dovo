# `dovo init`

The `dovo init` command initializes a project repository for Dovo (`dovo`), provisioning the `.dovo/` directory structure, a stable project identity, canonical configuration defaults, a local `.dovo/.gitignore`, catalog folders, and centrally stored runtime state scoped to the project.

## Usage

```bash
dovo init [OPTIONS]
```

## Options

| Flag | Description |
| --- | --- |
| `--id <ID>` | Explicit unique project ID slug. Must match `^[a-z0-9][a-z0-9-_]{2,62}$`; an invalid value exits with code `2`. When omitted, a friendly slug is generated. |
| `--display-name <NAME>` | Human-readable project display name. |
| `--force` | Overwrite an existing `project.json`'s id when `--id` is also provided. Has no effect on its own, and never touches `config.json`. |
| `--format <terminal\|json>` | Presentation format (`terminal` or `json`). Defaults to `terminal`. |
| `--repair` | Add missing required config keys without overwriting user values. |
| `--overwrite` | Replace an existing config with fresh V1 defaults (destructive). |

### `--id` / `--display-name` / `--force`

`dovo init` writes `<repo>/.dovo/project.json` containing a stable project identity (`id`, `display_name`, `created_at`). Without `--id`, a generated slug (e.g. `brave-otter`) is used. Rerunning `dovo init` preserves an existing identity unless both `--id` and `--force` are passed together, in which case the identity is overwritten with the new `--id`. `--force` alone, without `--id`, has no effect on an existing identity or on `config.json`.

```bash
dovo init --id my-project --display-name "My Project"
dovo init --id my-project --force   # only effective on a rerun that changes the id
```

### `.dovo/.gitignore`

`dovo init` pre-seeds `<repo>/.dovo/.gitignore`, scoping the local ignore rules to `.dovo/` itself rather than the repository root `.gitignore`. See `DOVO_LOCAL_GITIGNORE_ENTRIES` for the exact tracked and ignored entries; the repository-root `.gitignore` is never modified by `dovo init`.

### `--repair`

Non-destructively repairs an existing configuration file. It scans `.dovo/config.json` and inserts any missing schema keys using default V1 canonical values, preserving your existing user configurations and initialization timestamps.

```bash
dovo init --repair
```

### `--overwrite`

Destructively overwrites an existing `.dovo/config.json` file with fresh canonical V1 defaults. Does not affect the project identity in `project.json`.

```bash
dovo init --overwrite
```

### `--format`

Specifies the output presentation format. When set to `json`, emits structured NDJSON envelopes suitable for desktop and UI integrations.

```bash
dovo init --format json
```

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

Emits a structured NDJSON payload (see `WorkspaceInitView` for the full field list):

```json
{"event_type": "WorkspaceInitResult", "payload": {"ok": true, "root_path": "/path/to/my-repo/.dovo", "root_path_relative": ".dovo", "bootstrap_outcome": "initialized", "dirs_created": [".dovo/.meta"], "project_id": "my-project", "identity_path_relative": ".dovo/project.json", "identity_preserved": false, "gitignore_path_relative": ".dovo/.gitignore", "gitignore_tracked_entries": ["config.json", "project.json", "catalog/"], "config_created": true, "config_overwritten": false, "config_repaired": false, "config_skipped_existing": false, "config_path_relative": ".dovo/config.json", "inserted_keys": [], "seeded_files": [".dovo/catalog/blueprints/dovo/fix-tests.yml", ".dovo/catalog/blueprints/dovo/review-fix.yml", ".dovo/catalog/steps/dovo/ai-code-patcher.yml", ".dovo/catalog/steps/dovo/ai-planner.yml", ".dovo/catalog/steps/dovo/ai-reviewer.yml", ".dovo/catalog/steps/dovo/git-sync-base.yml", ".dovo/catalog/steps/dovo/run-tests.yml"], "skipped_seed_files": [], "overwritten_seed_files": [], "failure_mode": null, "errors": [], "warnings": [], "fixes": []}}
```
