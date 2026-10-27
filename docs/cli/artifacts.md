# `dovo artifacts`

The `dovo artifacts` command group inspects and manages session artifacts: named file bundles published from a running blueprint's worktree into persistent storage, so they can be listed, downloaded, and eventually pruned once expired.

Artifacts live under the global storage root: `~/.dovo/storage/projects/<project_id>/artifacts/<session_id>/<name>/<revision>/` (the root honors `DOVO_HOME`). Every publish of the same `<name>` in a session adds a new, immutable revision (1, 2, 3, ...); earlier revisions are never rewritten. Each revision carries a `manifest.json` listing its revision number, bundle digest, the publishing step's `step_id`, `attempt` and `loop_iteration`, and every file's relative path, SHA256 checksum, and size.

There is no `dovo artifacts upload` command. Publishing happens two ways, both driven from inside a running blueprint:

- A step referencing the seeded catalog step `dovo/upload-artifact` (`type: internal`, `command: artifacts.upload`), parameterized via `env:` (`ARTIFACT_NAME`, `ARTIFACT_PATH`, optional `ARTIFACT_RETENTION_DAYS`).
- A step declaring a declarative `artifacts:` block (`name`, `path`, optional `retention_days`, optional `required`), published after the step succeeds — this applies identically to a top-level step and to a loop `do:` sub-step. A `required` entry (the default) that matches no files fails the step with `Artifact '<name>' matched no files at '<path>'.` and the step's `on_failure` policy applies; any other publish failure fails it with `Failed to publish artifact '<name>': <detail>`. With `required: false` both are warnings.

Symmetrically, `dovo/download-artifact` (`command: artifacts.download`, `env:` keys `ARTIFACT_NAME`, `ARTIFACT_DEST`, optional `ARTIFACT_SESSION_ID`, optional `ARTIFACT_REVISION`) downloads a published revision (the latest by default) back into the running worktree from inside a blueprint. `ARTIFACT_REVISION` must be a positive integer.

Later steps can reference the latest revision with `{{ artifacts.<name>.revision }}`, `.digest`, `.dir` and `.content`; see [Working with Steps](../guides/working-with-steps.md#interpolation-paths).

## Usage

```bash
dovo artifacts list [OPTIONS]
dovo artifacts download <session_id> <name> --dest <path> [--revision <n>] [OPTIONS]
dovo artifacts prune [OPTIONS]
```

### `dovo artifacts list`

| Flag | Description |
| --- | --- |
| `--session <session_id>` | Restrict the listing to one session. |
| `--format [terminal\|json]` | Presentation format (`terminal` or `json`). |

The listing shows one row per revision, newest first. Legacy rows published before revisions existed show as revision 1 with `-` for the digest and provenance columns. The JSON format carries each row's full digest and `attempt`.

### `dovo artifacts download`

| Argument / Flag | Description |
| --- | --- |
| `session_id` | Required session identifier the artifact was published under. |
| `name` | Required artifact name to download. |
| `--dest <path>` | Required host destination directory to extract files into. |
| `--revision <n>` | Revision to download (an integer of at least 1). Defaults to the latest revision. |
| `--format [terminal\|json]` | Presentation format (`terminal` or `json`). |

Every file's SHA256 is checksum-verified against `manifest.json` before anything is copied; a mismatch on any single file leaves `--dest` untouched.

### `dovo artifacts prune`

| Flag | Description |
| --- | --- |
| `--dry-run` | Report what would be pruned without mutating the filesystem or database. |
| `--force` | Prune expired artifacts even when `prune.remove_expired_artifacts` is `false` in `.dovo/config.json`. |
| `--format [terminal\|json]` | Presentation format (`terminal` or `json`). |

Pruning deletes artifact revisions whose `expires_at` (set from a step's `retention_days`) is in the past; each revision expires on its own, so pruning revision 1 leaves revision 2 untouched. Without `--force`, `dovo artifacts prune` is a no-op when `prune.remove_expired_artifacts` is `false` (the default). `retention_days=0` (or omitted) means an artifact never expires.

## Errors

`dovo artifacts download` exits `1` when:

- no artifact named `<name>` exists for `<session_id>` (`Artifact '<name>' not found for session '<session_id>'`),
- `--revision <n>` names a revision that does not exist (`Artifact '<name>' revision <n> not found for session '<session_id>'`),
- a stored file's live checksum no longer matches `manifest.json`,
- the destination cannot be written to (for example, a permissions error).

`dovo artifacts prune` exits `1` only when the advisory workspace lock cannot be acquired; a disabled prune pass (no `--force`) exits `0`.

## Examples

List every published artifact for the current project:

```bash
dovo artifacts list
```

A republished artifact shows one row per revision:

```text
Session            Artifact  Rev  Digest    Step  Iter  Files  Size
blueprint_a1b2c3d4 plan      2    b07d44aa  plan  2     1      2.4 KB
blueprint_a1b2c3d4 plan      1    3fa9c2e1  plan  1     1      2.1 KB
```

List artifacts published under one session:

```bash
dovo artifacts list --session wf_a1b2c3d4
```

Download a published artifact bundle:

```bash
dovo artifacts download wf_a1b2c3d4 dist-packages --dest ./dist
```

Download revision 1 of a republished artifact:

```bash
dovo artifacts download wf_a1b2c3d4 plan --dest ./plan-v1 --revision 1
```

Preview which expired artifacts would be pruned:

```bash
dovo artifacts prune --dry-run
```

Prune expired artifacts regardless of the config toggle:

```bash
dovo artifacts prune --force
```
