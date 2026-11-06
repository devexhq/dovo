# `dovo step`

The `dovo step` command inspects, creates, deletes, and validates executable step YAML files, resolved across four precedence tiers: REPO (`.dovo/catalog/steps/`), USER (`~/.dovo/user/catalog/steps/`), GLOBAL (`~/.dovo/global/catalog/steps/`), and PACKAGED (bundled starter templates). It works like [`dovo blueprint`](blueprint.md), including tier precedence, but for steps.

`dovo step` requires an explicit subcommand; there is no bare-invocation default.

With `--format json`, every `dovo step` payload carries `item_type: "step"`, on success and on failure, so a consumer can tell it from a [`dovo blueprint`](blueprint.md) payload.

When `list`, `show`, `create`, `delete` or `validate` fails with an unexpected error, or with an operating-system error writing or deleting a catalog file, and Dovo writes a [diagnostic log](doctor.md#diagnostic-logs) record for it, the error message is unchanged and a `Details logged: <path> (event <event_id>)` line naming that file follows it. With `--format json`, the record is listed in `diagnostics` as `{"event_id": …, "log_path": …}`. If the record cannot be written, there is no line and `diagnostics` is empty.

If another Dovo process holds the workspace lock and the wait times out, `create` and `delete` print a Workspace Lock Timeout panel, or with `--format json` an `error` event with `error_code` `WORKSPACE_LOCK_TIMEOUT` (see [JSON Output](../reference/json-output.md#the-error-event)), and exit with code 1; wait for that process to finish and retry.

## Subcommands

### `dovo step list` / `dovo step ls`

Lists step items across all tiers, each row tagged with its resolved `tier`.

```bash
dovo step list [--format terminal|json]
dovo step ls [--format terminal|json]
```

### `dovo step create`

Creates a new step file, seeded from the packaged `default.yml` scaffold. By default it writes under the REPO tier (`.dovo/catalog/steps/<name>.yml`). Pass `--user` to write into the USER tier (`~/.dovo/user/catalog/steps/<name>.yml`) or `--global` for the GLOBAL tier (`~/.dovo/global/catalog/steps/<name>.yml`) instead; the two flags are mutually exclusive: passing both fails with `--user and --global are mutually exclusive.`, exits `2` (`CATALOG_TIER_CONFLICT` with `--format json`) and writes no file.

```bash
dovo step create --name <name> [--user | --global] [--format terminal|json]
```

### `dovo step show`

Displays metadata and YAML content for a step, resolved via tier precedence and scoped to steps only (a same-named blueprint is not returned).

```bash
dovo step show <sha_or_name> [--format terminal|json]
```

### `dovo step delete`

Deletes a REPO-tier step file and reindexes that tier. The name or sha is looked up among steps only: a blueprint with that name or sha is never deleted, and a name that matches only a blueprint fails like an unknown name, with `Catalog step '<name>' not found.` and exit `1` (`CATALOG_NOT_FOUND` with `--format json`). The tier and namespace checks that follow judge only matching steps. A match resolved from USER or GLOBAL tier is refused rather than deleted. Bundled templates in the `dovo/` namespace are protected and cannot be deleted regardless of tier. Without `--force`, it asks for confirmation on stderr once the name resolves to a catalog item it can delete, also with `--format json`, so stdout holds only the result line; an unknown name, a protected template or a USER or GLOBAL match fails with exit `1` without asking. Declining, or closing the prompt without an answer, deletes nothing, exits `0` and prints `Deletion cancelled.`; with `--format json`, the payload has `cancelled: true`, `deleted: false`, and the kept step in `item`.

```bash
dovo step delete <sha_or_name> [--force] [--format terminal|json]
```

### `dovo step validate`

Validates a step definition (YAML syntax, schema, and semantic invariants such as an unsafe `script_path` or a placeholder that is not a valid expression) without executing it.

```bash
dovo step validate <target> [--format terminal|json]
```

`target` is a catalog name or namespaced identifier, or a relative/absolute file path. A catalog name matches steps only. There is no `--type` option.

Exits `0` when the definition is valid and `1` on any failure, including a target that names no catalog item.

## Exit codes

Every `dovo step` subcommand exits by its result's `error_code`:

- `0`: the command succeeded, including a `delete` whose confirmation you decline.
- `2`: a usage error, such as a missing required option or an unknown option, or `CATALOG_TIER_CONFLICT` (`create` with `--user` and `--global`).
- `1`: every other failure, such as `CATALOG_NOT_FOUND` or a definition that fails `validate`, and every failure reported as an `error` event: a workspace without a valid `.dovo/project.json`, a configuration that does not load, a workspace lock timeout or an unexpected error.

See [JSON Output](../reference/json-output.md#exit-codes) for the rule every command follows.
