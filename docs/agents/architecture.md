# Architecture

Structural map for agents: which packages exist, who may import whom, and the procedures for adding a command, a catalog-backed domain, or an agent provider. **File placement rules** (where models vs services live) are in
[code-conventions.md](code-conventions.md#core-package-layout).
User-facing command behavior lives under [docs/cli/](../cli/). Cross-cutting contracts (config merge, paths, persistence authority, secrets) live in [schemas.md](schemas.md).

Update this doc only when a package is added, removed, or moved, or an import boundary changes. Adding files or classes inside an existing package needs no edit here.

## Layers

**Relevant sources:** `src/dovo/cli/`, `src/dovo/engine/`, `src/dovo/core/`, `src/dovo/common/`, `src/dovo/schemas/`.

Every package directory under these four layers must appear below, enforced by [`tests/lint/test_architecture_tree_parity.py`](../../tests/lint/test_architecture_tree_parity.py).

```
src/dovo/cli/                    Typer entrypoint and subcommand wrappers (no domain logic)
  artifacts/ blueprint/ config/ diff/ doctor/ history/ init/ logs/ resume/ run/ status/ step/ worktree/
  ui/                                Dispatcher, events, and formatters; presentation only

src/dovo/core/                   Domain logic (no Typer, no engine)
  agents/                            Provider base class, registry, provider integrations
  artifacts/                         Session artifact publish, list, download, prune
  bootstrap/                         Workspace directory initialization and repair
  catalog/                           Disk-only multi-tier blueprint/step templates and definitions
  config/                            Config loading, validation, generation, mutation
  db/                                SQLite connection, migrations, repositories
  diagnostics/                       Doctor check registry and runner
  git/                               Git subprocess and diff parsing
  inputs/                            Parameter declaration, resolution, interpolation
  project/                           Project identity
  sessions/                          Session and history entrypoints, session.log reading
  status/                            Workspace health collection
  worktree/                          Git worktree lifecycle

src/dovo/engine/                 Run execution: Engine facade, run coordinator, session lifecycle
  executors/                         Single-step execution
  services/                          Run and resume services

src/dovo/common/                 Shared foundations (never imports core/ or cli/)
  filesystem/                        Atomic writes, path models, safe YAML I/O

src/dovo/schemas/v1/             Packaged, versioned JSON Schemas
```

- New domain code: `models.py` + `services/<verb>.py`. Do not extend the flat `config/` / `db/` pattern.
- Single-step execution lives in `engine/executors/`; multi-step orchestration in `engine/` (`RunCoordinator`, driven by `drive_run`); the process facade is `Engine.run` / `Engine.resume`.

### Ownership notes

Only what is not obvious from the tree:

- **Worktree** owns worktree lifecycle policy; `engine/` consumes it.
- **Catalog** owns the authored definitions (`StepDefinition`, `BlueprintDefinition`, `LoopStepBlock`), not `engine/`.
- **Engine** owns run persistence, session minting, and canonical execution state; it appends `session.log` events that `core/sessions` reads.
- **Artifacts**: `core/artifacts` owns publish, download, and prune; the `type: internal` upload/download handlers and the declarative `artifacts:` auto-publish live in `engine/`.
- **Agents**: provider modules are leaves of `base.py` → `cli_mutation.py` → provider → `registry.py`/`factory.py`; no module imports one to its right (a cycle fails `basedpyright`).

### Package boundaries (import direction)

Dependencies flow one way down the stack; do not import upward:

```
common/  ->  core/  ->  engine/  ->  cli/
core/project/  ->  core/{db,git,worktree,catalog,inputs,sessions,status,artifacts}/  ->  core/agents/  ->  core/diagnostics/
```

Banned imports per package are the table `LAYER_RULES` in [`tests/lint/test_layer_direction.py`](../../tests/lint/test_layer_direction.py); read it for the full list. Beyond the layer flow:

- `core/` and `common/` never import `rich`; terminal rendering is driven through `ui_dispatcher.dispatch(result)`.
- `core/git/` and `core/project/` import no other `core/` package.
- `core/inputs/` and `core/catalog/` must not import `agents/`; `core/agents/` must not import `config/`.
- `core/config/validate.py` may import `core/agents/registry`.
- `engine/executors/` must not import engine modules outside `executors/`.
- Known exceptions, tracked in `LAYER_EXCEPTIONS` and only ever removed: `common/filesystem/models.py` imports `dovo.core.catalog.models` for an annotation under `TYPE_CHECKING`, and `engine/executors/agent_step.py` imports `dovo.engine.session_log`.

Not mechanically checked (review-time):

- CLI commands never render directly or import formatters; they emit results through `ui_dispatcher.dispatch(result)`.
- `cli/ui/` must not originate a domain fact. All domain facts, outcomes, warnings, and remediations originate in `core/` or `common/`; `cli/ui/` only derives presentation views.

## Adding a new command

**Relevant sources:** `src/dovo/cli/`, `src/dovo/cli/ui/`, `src/dovo/cli/cli.py`

1. Create `src/dovo/cli/<name>/` with `app.py` and `commands/<action>.py` (or `commands/root.py`).
2. Add formatters under `src/dovo/cli/ui/formatters/<name>/` implementing `transform()` and `to_rich()`, with view models for derived values, and register them in that package's `__init__.py`.
3. Wire command logic to domain services or facades and dispatch results via `ui_dispatcher.dispatch(result)`. Keep CLI packages free of business logic, DB queries, or direct filesystem scans.
4. Register the command in [src/dovo/cli/cli.py](../../src/dovo/cli/cli.py) and list it in `README.md` ([`tests/lint/test_readme_command_parity.py`](../../tests/lint/test_readme_command_parity.py) fails otherwise).
5. Add tests under `tests/cli/<name>/` and the per-command page under [docs/cli/](../cli/).

## Adding a new catalog-backed domain

1. **Models**: `<X>Definition` in `core/catalog/definitions/<x>.py`.
2. **Exceptions**: `<X>LoadError` / `<X>ValidationError` subclassing definition errors in `core/catalog/exceptions.py`.
3. **Handle**: `core/catalog/<x>.py` -> `Catalog.get(..., item_type=..., definition_cls=...)`.
4. **Execution**: If executing steps, run them through `Engine.run` (via a run service); do not add a separate step loop.
5. **CLI**: Thin `commands/root.py`, UI formatters in `cli/ui/formatters/<x>/`.

## Adding a new agent provider

**Relevant sources:** `src/dovo/core/agents/`, `src/dovo/core/config/models.py`

1. Add the provider token to `AgentProvider` in `core/config/models.py` and to the `agent.provider` enum in `schemas/v1/config.json` ([`tests/lint/test_config_schema_parity.py`](../../tests/lint/test_config_schema_parity.py) fails if they differ).
2. Direct-mutation providers (the provider CLI or SDK edits the worktree, like `copilot`) subclass `CliDirectMutationAdapter`; others subclass `BaseAgentProvider` and implement `_invoke`. Neither overrides `invoke`, which runs the credential check first. Declare every credential env var in `credential_envs`, including names without a secret suffix, because response redaction reads that list.
3. Resolve secrets via a module-level `resolve_<provider>_api_key()` from environment variables, never from `config.json`.
4. Declare the `ProviderSpec` in the provider module, import it in `core/agents/registry.py`, and add it to `PROVIDERS`.
5. Add tests under `tests/core/agents/test_<provider>.py` with fake execution functions or transports, and the setup failure modes to [troubleshooting.md](troubleshooting.md).

## Secrets handling

**Relevant sources:** `src/dovo/core/agents/`

- API keys are resolved from the environment at call time from the descriptor's `credential_envs` via `resolve_credential`.
- Secrets are never accepted as `config.json` fields, never persisted to the centralized database, and never passed into prompt builders.
- `common/redact.py` (`SecretRedactor`) is the single masking implementation for agent responses, step captures, `diff.patch`, and session history reads.

## The `.dovo/` directory

Created and repaired idempotently by [core/bootstrap](../../src/dovo/core/bootstrap/):

```
.dovo/
  .meta/bootstrap.json
  .lock                       # cross-process advisory lock
  .gitignore                  # local; ignores .meta/, .lock, worktrees/, *.db*
  config.json                 # schemas/v1/config.json
  project.json                # schemas/v1/project.json; stable project identity
  catalog/                    # blueprints/, steps/ + seeded dovo/ templates; index.json is a derived cache, never hand-edited
  worktrees/                  # git worktree checkouts, .dovo/worktrees/<session_id>/
```

`sessions/`, `artifacts/`, `tmp/`, and `logs/` are never created under `.dovo/`; that project-scoped runtime state lives only under the global `DOVO_HOME` storage root and requires a project identity. Agent steps allocate their scratch and control directories beneath the session temp directory there, never in a checkout. Path ownership and the database layout are contracts in [schemas.md](schemas.md).

Schemas (`schemas/v1/`) and catalog templates (`core/catalog/templates/`) ship inside the package and are loaded via `importlib.resources`.
