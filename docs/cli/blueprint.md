# `dovo blueprint`

The `dovo blueprint` command inspects, creates, deletes, and validates executable blueprint YAML files, resolved across four precedence tiers: REPO (`.dovo/catalog/blueprints/`), USER (`~/.dovo/user/catalog/blueprints/`), GLOBAL (`~/.dovo/global/catalog/blueprints/`), and PACKAGED (bundled starter templates). No SQLite index is involved — each disk-backed tier keeps its own `index.json`, rebuilt wholesale from a directory walk before every lookup, so a command always sees the current contents of disk.

`dovo blueprint` requires an explicit subcommand; there is no bare-invocation default (unlike the retired `dovo catalog`).

## Tier precedence

When a name or SHA matches more than one tier, the REPO copy wins, then USER, then GLOBAL, then PACKAGED. A REPO-tier blueprint shadowing a USER-tier blueprint of the same name is expected layering, not an error; a duplicate match *within the same tier* produces a warning instead.

## Subcommands

### `dovo blueprint list` / `dovo blueprint ls`

Lists blueprint items across all tiers, each row tagged with its resolved `tier`.

```bash
dovo blueprint list [--format terminal|json]
dovo blueprint ls [--format terminal|json]
```

### `dovo blueprint create`

Creates a new blueprint file, seeded from the packaged `default.yml` scaffold. By default it writes under the REPO tier (`.dovo/catalog/blueprints/<name>.yml`). Pass `--user` to write into the USER tier (`~/.dovo/user/catalog/blueprints/<name>.yml`) or `--global` for the GLOBAL tier (`~/.dovo/global/catalog/blueprints/<name>.yml`) instead; the two flags are mutually exclusive.

```bash
dovo blueprint create --name <name> [--user | --global] [--format terminal|json]
```

### `dovo blueprint show`

Displays metadata and YAML content for a blueprint, resolved via the tier precedence above and scoped to blueprints only (a same-named step is not returned).

```bash
dovo blueprint show <sha_or_name> [--format terminal|json]
```

### `dovo blueprint delete`

Deletes a REPO-tier blueprint file and reindexes that tier. A match resolved from USER or GLOBAL tier is refused with a "not deletable from this tier" error rather than deleted, since a repo-scoped command should never mutate shared state outside its own repository. Bundled templates in the `dovo/` namespace (e.g. `dovo/fix-tests`) are protected and cannot be deleted regardless of tier.

```bash
dovo blueprint delete <sha_or_name> [--force] [--format terminal|json]
```

### `dovo blueprint validate`

Validates a blueprint definition (YAML syntax, schema, and semantic invariants such as duplicate step IDs, undeclared input placeholders, and unsafe `script_path` values) without executing it. See [`CatalogValidateResult`](../../src/dovo/core/catalog/models.py) for the result shape.

```bash
dovo blueprint validate <target> [--format terminal|json]
```

`target` is a catalog name or namespaced identifier (e.g. `commit-plan`, `dovo/fix-tests`), or a relative/absolute file path. Unlike the retired `dovo catalog validate`, there is no `--type` option: the command name itself already fixes the type.
