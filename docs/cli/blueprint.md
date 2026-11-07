# `dovo blueprint`

The `dovo blueprint` command inspects, creates, deletes, and validates executable blueprint YAML files, resolved across four precedence tiers: REPO (`.dovo/catalog/blueprints/`), USER (`~/.dovo/user/catalog/blueprints/`), GLOBAL (`~/.dovo/global/catalog/blueprints/`), and PACKAGED (bundled starter templates).

`dovo blueprint` requires an explicit subcommand; there is no bare-invocation default.

With `--format json`, every `dovo blueprint` payload carries `item_type: "blueprint"`, on success and on failure, so a consumer can tell it from a [`dovo step`](step.md) payload.

When `list`, `show`, `create`, `delete` or `validate` fails with an unexpected error, or with an operating-system error reading, writing or deleting a catalog file, and Dovo writes a [diagnostic log](doctor.md#diagnostic-logs) record for it, the error message is unchanged and a `Details logged: <path> (event <event_id>)` line naming that file follows it. With `--format json`, the record is listed in `diagnostics` as `{"event_id": …, "log_path": …}`. If the record cannot be written, there is no line and `diagnostics` is empty.

If another Dovo process holds the workspace lock and the wait times out, `create` and `delete` print a Workspace Lock Timeout panel, or with `--format json` an `error` event with `error_code` `WORKSPACE_LOCK_TIMEOUT` (see [JSON Output](../reference/json-output.md#the-error-event)), and exit with code 1; wait for that process to finish and retry.

## Tier precedence

When a name or SHA matches more than one tier, the REPO copy wins, then USER, then GLOBAL, then PACKAGED. A REPO-tier blueprint shadowing a USER-tier one of the same name is normal; a duplicate match *within the same tier* produces a warning.

## Subcommands

### `dovo blueprint list` / `dovo blueprint ls`

Lists blueprint items across all tiers, each row tagged with its resolved `tier`.

```bash
dovo blueprint list [--format terminal|json]
dovo blueprint ls [--format terminal|json]
```

A blueprint file that exists but cannot be read, for example because of its permissions, is left out of the list with the warning `Failed to read catalog blueprint '<path>': <reason>`, and the list still exits `0`. The read error is recorded in the [diagnostic log](doctor.md#diagnostic-logs): a `Details logged: <path> (event <event_id>)` line names it, and with `--format json` the record is listed in `diagnostics`.

### `dovo blueprint create`

Creates a new blueprint file, seeded from the packaged `default.yml` scaffold. By default it writes under the REPO tier (`.dovo/catalog/blueprints/<name>.yml`). Pass `--user` to write into the USER tier (`~/.dovo/user/catalog/blueprints/<name>.yml`) or `--global` for the GLOBAL tier (`~/.dovo/global/catalog/blueprints/<name>.yml`) instead; the two flags are mutually exclusive: passing both fails with `--user and --global are mutually exclusive.`, exits `2` (`CATALOG_TIER_CONFLICT` with `--format json`) and writes no file.

```bash
dovo blueprint create --name <name> [--user | --global] [--format terminal|json]
```

### `dovo blueprint show`

Displays metadata and YAML content for a blueprint, resolved via the tier precedence above and scoped to blueprints only (a same-named step is not returned).

```bash
dovo blueprint show <sha_or_name> [--format terminal|json]
```

When the only blueprint file with that name exists but cannot be read, `show` and `validate` fail with `Failed to read catalog blueprint '<path>': <reason>` and exit `1` (`CATALOG_FILE_UNREADABLE` with `--format json`) rather than reporting an unknown name; a readable blueprint of that name in another tier still wins.

### `dovo blueprint delete`

Deletes a REPO-tier blueprint file and reindexes that tier. The name or sha is looked up among blueprints only: a step with that name or sha is never deleted, and a name that matches only a step fails like an unknown name, with `Catalog blueprint '<name>' not found.` and exit `1` (`CATALOG_NOT_FOUND` with `--format json`). The tier and namespace checks that follow judge only matching blueprints. A match resolved from USER or GLOBAL tier is refused with an "only repo-tier items can be deleted" error. Bundled templates in the `dovo/` namespace (e.g. `dovo/fix-tests`) are protected and cannot be deleted regardless of tier. Without `--force`, it asks for confirmation on stderr once the name resolves to a catalog item it can delete, also with `--format json`, so stdout holds only the result line; an unknown name, a protected template or a USER or GLOBAL match fails with exit `1` without asking. Declining, or closing the prompt without an answer, deletes nothing, exits `0` and prints `Deletion cancelled.`; with `--format json`, the payload has `cancelled: true`, `deleted: false`, and the kept blueprint in `item`.

```bash
dovo blueprint delete <sha_or_name> [--force] [--format terminal|json]
```

### `dovo blueprint validate`

Validates a blueprint definition (YAML syntax, schema, and semantic invariants such as duplicate step IDs, undeclared input placeholders, placeholders that are not valid expressions, unsafe `script_path` values, unknown loop keys, and `when` and `if` conditions that reference steps out of scope) without executing it. A schema error in a step reports only the error for the kind of entry it declares, a step, a `type: loop` block or a `type: if` block, located by its position, as in `steps.0: …`. [`dovo run`](run.md) and [`dovo resume`](session.md#dovo-session-resume) apply the same checks before any step runs.

```bash
dovo blueprint validate <target> [--format terminal|json]
```

`target` is a catalog name or namespaced identifier (e.g. `commit-plan`, `dovo/fix-tests`), or a relative/absolute file path. A catalog name matches blueprints only. There is no `--type` option.

Exits `0` when the definition is valid and `1` on any failure, including a target that names no catalog item.

The result header alone reports the outcome. In the terminal, the command prints the target, its type and resolved path, then an Errors table with each error's code and fix and a Warnings table, and a valid definition ends with `Blueprint is valid.` With `--format json`, `ok` is `true` and `error_code` is `null` exactly when the definition is valid; the payload adds `target`, `resolved_path` and `item_type`.

## Error codes

With `--format json`, a failure reports one of these codes as `error_code`, and a success reports `null`.

| Code | Commands | Cause |
| --- | --- | --- |
| `WORKSPACE_NOT_INITIALIZED` | all | The workspace has no valid `.dovo/project.json`; run `dovo init`. Reported as an [`error` event](../reference/json-output.md#the-error-event). |
| `CONFIG_NOT_FOUND`, `CONFIG_UNREADABLE`, `CONFIG_PATH_INVALID`, `CONFIG_JSON_INVALID`, `CONFIG_ROOT_INVALID`, `CONFIG_SCHEMA_INVALID`, `CONFIG_TIER_UNREADABLE`, `CONFIG_TIER_JSON_INVALID`, `CONFIG_TIER_ROOT_INVALID`, `CONFIG_TIER_SCHEMA_INVALID` | all | The configuration does not load; [`dovo config show`](config.md#dovo-config-show) explains each code. Reported as an [`error` event](../reference/json-output.md#the-error-event). |
| `WORKSPACE_LOCK_TIMEOUT` | `create`, `delete` | Another `dovo` process held the workspace lock until the wait timed out. Reported as an [`error` event](../reference/json-output.md#the-error-event). |
| `INTERNAL_ERROR` | all | An unexpected error, with a [diagnostic log](doctor.md#diagnostic-logs) record. Reported in the result, or as an [`error` event](../reference/json-output.md#the-error-event) when it ends the command outside its result. |
| `CATALOG_NOT_FOUND` | `show`, `delete`, `validate` | The name or sha matches no blueprint, including a name that matches only a step, or a `validate` file target does not exist. |
| `CATALOG_FILE_UNREADABLE` | `show`, `validate` | The blueprint file cannot be read, for example because of its permissions. |
| `CATALOG_SYNTAX_INVALID` | `validate` | The file is not valid YAML, or its root is not a mapping. |
| `CATALOG_SCHEMA_INVALID` | `validate` | The definition fails the schema or a semantic check; `errors` lists every finding. |
| `CATALOG_ALREADY_EXISTS` | `create` | A blueprint file with that name already exists in the target tier. |
| `CATALOG_TIER_CONFLICT` | `create` | `--user` and `--global` were both given. Exits `2`. |
| `CATALOG_WRITE_FAILED` | all | The operating system refused to write or delete a catalog file, or to write the catalog index. |
| `CATALOG_TIER_DELETE_FORBIDDEN` | `delete` | The match resolved from the USER or GLOBAL tier; only REPO-tier items can be deleted. |
| `CATALOG_PROTECTED` | `delete` | The match is a bundled template in the `dovo/` namespace. |

## Exit codes

Every `dovo blueprint` subcommand exits by its result's `error_code`:

- `0`: the command succeeded, including a `delete` whose confirmation you decline.
- `2`: a usage error, such as a missing required option or an unknown option, or `CATALOG_TIER_CONFLICT` (`create` with `--user` and `--global`).
- `1`: every other failure, such as `CATALOG_NOT_FOUND` or a definition that fails `validate`, and every failure reported as an `error` event: a workspace without a valid `.dovo/project.json`, a configuration that does not load, a workspace lock timeout or an unexpected error.

See [JSON Output](../reference/json-output.md#exit-codes) for the rule every command follows.
