# `dovo step`

The `dovo step` command inspects, creates, deletes, and validates executable step YAML files, resolved across four precedence tiers: REPO (`.dovo/catalog/steps/`), USER (`~/.dovo/user/catalog/steps/`), GLOBAL (`~/.dovo/global/catalog/steps/`), and PACKAGED (bundled starter templates). It works like [`dovo blueprint`](blueprint.md), including tier precedence, but for steps.

`dovo step` requires an explicit subcommand; there is no bare-invocation default.

## Subcommands

### `dovo step list` / `dovo step ls`

Lists step items across all tiers, each row tagged with its resolved `tier`.

```bash
dovo step list [--format terminal|json]
dovo step ls [--format terminal|json]
```

### `dovo step create`

Creates a new step file, seeded from the packaged `default.yml` scaffold. By default it writes under the REPO tier (`.dovo/catalog/steps/<name>.yml`). Pass `--user` to write into the USER tier (`~/.dovo/user/catalog/steps/<name>.yml`) or `--global` for the GLOBAL tier (`~/.dovo/global/catalog/steps/<name>.yml`) instead; the two flags are mutually exclusive.

```bash
dovo step create --name <name> [--user | --global] [--format terminal|json]
```

### `dovo step show`

Displays metadata and YAML content for a step, resolved via tier precedence and scoped to steps only (a same-named blueprint is not returned).

```bash
dovo step show <sha_or_name> [--format terminal|json]
```

### `dovo step delete`

Deletes a REPO-tier step file and reindexes that tier. A match resolved from USER or GLOBAL tier is refused rather than deleted. Bundled templates in the `dovo/` namespace are protected and cannot be deleted regardless of tier.

```bash
dovo step delete <sha_or_name> [--force] [--format terminal|json]
```

### `dovo step validate`

Validates a step definition (YAML syntax, schema, and semantic invariants such as an unsafe `script_path`) without executing it.

```bash
dovo step validate <target> [--format terminal|json]
```

`target` is a catalog name or namespaced identifier, or a relative/absolute file path. There is no `--type` option.
