# `dovo artifacts`

The `dovo artifacts` command group inspects and manages session artifacts: named file bundles published from a running blueprint's worktree into persistent storage, so they can be listed, downloaded, and eventually pruned once expired.

Artifacts live under the global storage root: `~/.dovo/storage/projects/<project_id>/artifacts/<session_id>/<name>/` (the root honors `DOVO_HOME`). Each published bundle carries a `manifest.json` listing every file's relative path, SHA256 checksum, and size — see [`ArtifactManifest`](../../src/dovo/core/artifacts/models.py).

There is no `dovo artifacts upload` command. Publishing happens two ways, both driven from inside a running blueprint:

- A step referencing the seeded catalog step `dovo/upload-artifact` (`type: internal`, `command: artifacts.upload`), parameterized via `env:` (`ARTIFACT_NAME`, `ARTIFACT_PATH`, optional `ARTIFACT_RETENTION_DAYS`).
- A step declaring a declarative `artifacts:` block (`name`, `path`, optional `retention_days`), auto-published on success — this applies identically to a top-level step and to a loop `do:` sub-step, and a publish failure is recorded as a non-fatal warning rather than failing the step.

Symmetrically, `dovo/download-artifact` (`command: artifacts.download`, `env:` keys `ARTIFACT_NAME`, `ARTIFACT_DEST`, optional `ARTIFACT_SESSION_ID`) downloads a published bundle back into the running worktree from inside a blueprint.

## Usage

```bash
dovo artifacts list [OPTIONS]
dovo artifacts download <session_id> <name> --dest <path> [OPTIONS]
dovo artifacts prune [OPTIONS]
```

### `dovo artifacts list`

| Flag | Description |
| --- | --- |
| `--session <session_id>` | Restrict the listing to one session. |
| `--format [terminal\|json]` | Presentation format (`terminal` or `json`). |

### `dovo artifacts download`

| Argument / Flag | Description |
| --- | --- |
| `session_id` | Required session identifier the artifact was published under. |
| `name` | Required artifact name to download. |
| `--dest <path>` | Required host destination directory to extract files into. |
| `--format [terminal\|json]` | Presentation format (`terminal` or `json`). |

Every file's SHA256 is checksum-verified against `manifest.json` before anything is copied; a mismatch on any single file leaves `--dest` untouched.

### `dovo artifacts prune`

| Flag | Description |
| --- | --- |
| `--dry-run` | Report what would be pruned without mutating the filesystem or database. |
| `--force` | Prune expired artifacts even when `prune.remove_expired_artifacts` is `false` in `.dovo/config.json`. |
| `--format [terminal\|json]` | Presentation format (`terminal` or `json`). |

Pruning deletes artifacts whose `expires_at` (set from a step's `retention_days`) is in the past. Without `--force`, `dovo artifacts prune` is a no-op when `prune.remove_expired_artifacts` is `false` (the default) — see [`PruneConfig`](../../src/dovo/core/config/models.py). `retention_days=0` (or omitted) means an artifact never expires.

## Errors

`dovo artifacts download` exits `1` when:

- no artifact named `<name>` exists for `<session_id>` (`Artifact '<name>' not found for session '<session_id>'`),
- a stored file's live checksum no longer matches `manifest.json`,
- the destination cannot be written to (for example, a permissions error).

`dovo artifacts prune` exits `1` only when the advisory workspace lock cannot be acquired; a disabled prune pass (no `--force`) exits `0`.

## Examples

List every published artifact for the current project:

```bash
dovo artifacts list
```

List artifacts published under one session:

```bash
dovo artifacts list --session wf_a1b2c3d4
```

Download a published artifact bundle:

```bash
dovo artifacts download wf_a1b2c3d4 dist-packages --dest ./dist
```

Preview which expired artifacts would be pruned:

```bash
dovo artifacts prune --dry-run
```

Prune expired artifacts regardless of the config toggle:

```bash
dovo artifacts prune --force
```
