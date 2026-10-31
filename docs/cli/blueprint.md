# `dovo blueprint`

The `dovo blueprint` command inspects, creates, deletes, and validates executable blueprint YAML files, resolved across four precedence tiers: REPO (`.dovo/catalog/blueprints/`), USER (`~/.dovo/user/catalog/blueprints/`), GLOBAL (`~/.dovo/global/catalog/blueprints/`), and PACKAGED (bundled starter templates).

`dovo blueprint` requires an explicit subcommand; there is no bare-invocation default.

When `list`, `show`, `create`, `delete` or `validate` fails with an unexpected error, or with an operating-system error writing or deleting a catalog file, and Dovo writes a [diagnostic log](doctor.md#diagnostic-logs) record for it, the error message is unchanged and a `Details logged: <path> (event <event_id>)` line naming that file follows it. With `--format json`, the record is listed in `diagnostics` as `{"event_id": …, "log_path": …}`. If the record cannot be written, there is no line and `diagnostics` is empty.

If another Dovo process holds the workspace lock, `create` and `delete` exit with code 1 and print a Workspace Lock Timeout panel; wait for that process to finish and retry.

## Tier precedence

When a name or SHA matches more than one tier, the REPO copy wins, then USER, then GLOBAL, then PACKAGED. A REPO-tier blueprint shadowing a USER-tier one of the same name is normal; a duplicate match *within the same tier* produces a warning.

## Subcommands

### `dovo blueprint list` / `dovo blueprint ls`

Lists blueprint items across all tiers, each row tagged with its resolved `tier`.

```bash
dovo blueprint list [--format terminal|json]
dovo blueprint ls [--format terminal|json]
```

### `dovo blueprint create`

Creates a new blueprint file, seeded from the packaged `default.yml` scaffold. By default it writes under the REPO tier (`.dovo/catalog/blueprints/<name>.yml`). Pass `--user` to write into the USER tier (`~/.dovo/user/catalog/blueprints/<name>.yml`) or `--global` for the GLOBAL tier (`~/.dovo/global/catalog/blueprints/<name>.yml`) instead; the two flags are mutually exclusive: passing both exits `2` with a usage error and writes no file.

```bash
dovo blueprint create --name <name> [--user | --global] [--format terminal|json]
```

### `dovo blueprint show`

Displays metadata and YAML content for a blueprint, resolved via the tier precedence above and scoped to blueprints only (a same-named step is not returned).

```bash
dovo blueprint show <sha_or_name> [--format terminal|json]
```

### `dovo blueprint delete`

Deletes a REPO-tier blueprint file and reindexes that tier. A match resolved from USER or GLOBAL tier is refused with an "only repo-tier items can be deleted" error. Bundled templates in the `dovo/` namespace (e.g. `dovo/fix-tests`) are protected and cannot be deleted regardless of tier. Without `--force`, it asks for confirmation once the name resolves to a catalog item it can delete; an unknown name, a protected template or a USER or GLOBAL match fails with exit `1` without asking. Declining, or closing the prompt without an answer, deletes nothing, exits `0` and prints `Deletion cancelled.`; with `--format json`, the payload has `cancelled: true`.

```bash
dovo blueprint delete <sha_or_name> [--force] [--format terminal|json]
```

### `dovo blueprint validate`

Validates a blueprint definition (YAML syntax, schema, and semantic invariants such as duplicate step IDs, undeclared input placeholders, unsafe `script_path` values and unknown loop keys) without executing it. A schema error in a step reports only the error for the kind of entry it declares, a step or a `type: loop` block, located by its position, as in `steps.0: …`. [`dovo run`](run.md) applies the same checks before any step runs.

```bash
dovo blueprint validate <target> [--format terminal|json]
```

`target` is a catalog name or namespaced identifier (e.g. `commit-plan`, `dovo/fix-tests`), or a relative/absolute file path. A catalog name matches blueprints only. There is no `--type` option.

Exits `0` when the definition is valid and `1` on any failure, including a target that names no catalog item.
