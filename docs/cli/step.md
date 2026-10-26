# `dovo step`

The `dovo step` command inspects, creates, deletes, and validates executable step YAML files, resolved across four precedence tiers: REPO (`.dovo/catalog/steps/`), USER (`~/.dovo/user/catalog/steps/`), GLOBAL (`~/.dovo/global/catalog/steps/`), and PACKAGED (bundled starter templates). It works like [`dovo blueprint`](blueprint.md), including tier precedence, but for steps.

`dovo step` requires an explicit subcommand; there is no bare-invocation default.

When `list`, `show`, `create`, `delete` or `validate` fails with an unexpected error, or with an operating-system error writing or deleting a catalog file or reading the file to show, and Dovo writes a [diagnostic log](doctor.md#diagnostic-logs) record for it, the error message is unchanged and a `Details logged: <path> (event <event_id>)` line naming that file follows it. With `--format json`, the record is listed in `diagnostics` as `{"event_id": …, "log_path": …}`. If the record cannot be written, there is no line and `diagnostics` is empty.

If another Dovo process holds the workspace lock, `create` and `delete` exit with code 1 and print a Workspace Lock Timeout panel; wait for that process to finish and retry.

## Subcommands

### `dovo step list` / `dovo step ls`

Lists step items across all tiers, each row tagged with its resolved `tier`.

```bash
dovo step list [--format terminal|json]
dovo step ls [--format terminal|json]
```

### `dovo step create`

Creates a new step file, seeded from the packaged `default.yml` scaffold. By default it writes under the REPO tier (`.dovo/catalog/steps/<name>.yml`). Pass `--user` to write into the USER tier (`~/.dovo/user/catalog/steps/<name>.yml`) or `--global` for the GLOBAL tier (`~/.dovo/global/catalog/steps/<name>.yml`) instead; the two flags are mutually exclusive: passing both exits `2` with a usage error and writes no file.

```bash
dovo step create --name <name> [--user | --global] [--format terminal|json]
```

### `dovo step show`

Displays metadata and YAML content for a step, resolved via tier precedence and scoped to steps only (a same-named blueprint is not returned).

```bash
dovo step show <sha_or_name> [--format terminal|json]
```

### `dovo step delete`

Deletes a REPO-tier step file and reindexes that tier. A match resolved from USER or GLOBAL tier is refused rather than deleted. Bundled templates in the `dovo/` namespace are protected and cannot be deleted regardless of tier. Without `--force`, it asks for confirmation first. Declining, or closing the prompt without an answer, deletes nothing, exits `0` and prints `Deletion cancelled.`; with `--format json`, the payload has `cancelled: true`.

```bash
dovo step delete <sha_or_name> [--force] [--format terminal|json]
```

### `dovo step validate`

Validates a step definition (YAML syntax, schema, and semantic invariants such as an unsafe `script_path`) without executing it.

```bash
dovo step validate <target> [--format terminal|json]
```

`target` is a catalog name or namespaced identifier, or a relative/absolute file path. A catalog name matches steps only. There is no `--type` option.

Exits `0` when the definition is valid and `1` on any failure, including a target that names no catalog item.
