# Architecture

Structural map for agents. **File placement rules** (where models vs services live) are in
[code-conventions.md](code-conventions.md#core-package-layout).
User-facing command behavior lives under [docs/cli/](../cli/). Entity shapes and schemas live in [schemas.md](schemas.md).

## Layers

**Relevant sources:** `src/dovo/cli/`, `src/dovo/engine/`, `src/dovo/core/`, `src/dovo/common/`, `src/dovo/schemas/`.

```
src/dovo/cli/                    Typer CLI entrypoint and subcommand wrappers (no domain logic)
  cli.py                             Application definition, global options, top-level exception handling
  <name>/                            Subcommand packages (blueprint, config, diff, history, init, logs, resume, run, worktree, status, step)

src/dovo/core/                   Domain business logic and orchestration (no Typer imports)
  bootstrap/                         Workspace directory structure initialization and repair
  git/                               Low-level Git CLI subprocess invocation and plumbing, and unified-diff parsing/validation
  worktree/                           Isolated git worktree lifecycle (create, delete, list, prune, apply, diff)
  config/                            Workspace configuration loading, validation, generation, and mutation
  project/                           Stable project identity model, generation, and persistence services
  db/                                SQLite persistence, connection management, Alembic migrations, and repositories
  inputs/                            Parameter input declaration, CLI flag resolution, and placeholder interpolation
  catalog/                           Blueprint/step template discovery, disk-only multi-tier indexing, seeding, inventory, and the authored blueprint/step/condition definitions with their resolution services
  status/                            Workspace health diagnostics and telemetry collection
  artifacts/                         Session artifact publishing, listing, downloading, and pruning
  diagnostics/                       Diagnostic check registry, execution runner, and health validation engine
  sessions/                          Session domain packages (exports nothing; import from the subpackages)
    diff/                              Session unified diff computation and artifact retrieval
    history/                           Execution run queries and history presentation
    logs/                              Persisted session logs (run.log timeline events and appender, per-attempt step captures)
  agents/                            AI agent provider base class, descriptor registry, provider integrations (copilot), and the direct-mode attempt pipeline

src/dovo/engine/                 Execution engine: Engine facade, state-driven run coordinator, session lifecycle, run/resume services, and the executors/ package
  executors/                         Step execution (StepExecution), assertions, metadata, condition evaluation, internal command dispatch, agent step dispatch, and execution models

src/dovo/common/                 Shared foundational utilities (never imports core/ or cli/)
  filesystem/                        Atomic file operations, path helpers, safe YAML I/O
  schema_validation.py               JSON Schema Draft 2020-12 validation wrapper
  lock.py, process.py, utils.py      Cross-process advisory locks, subprocess helpers, and console formatters

src/dovo/schemas/v1/             Packaged, versioned JSON Schemas (config.json, project.json, workflow.json)
```

- Default for **new** domain code: `models.py` + `services/<verb>.py`. Do not extend the flat `config/` / `db/` pattern to new domains.
- Single-step execution: `engine/executors/` (`step_executor.py`).
- Multi-step orchestration: `engine/` (`RunCoordinator` in `coordinator.py`, driven by `drive_run` in `session.py`).
- Process facade: `engine/` (`Engine.run` / `Engine.resume`, `BlueprintRunService` / `BlueprintResumeService`).

### Domain ownership

**Relevant sources:** `src/dovo/core/`, `src/dovo/engine/`

- **Inputs** (`core/inputs/`): `ParameterInput`, CLI flag resolution, `${{ inputs.* }}` placeholder interpolation. Must not import catalog or agents.
- **Agents** (`core/agents/`): Provider base class (`BaseAgentProvider`), `ProviderSpec` registry (`PROVIDERS`), provider implementations (`copilot`), shared credential lookup (`credentials.py`), failure payload models, the direct-mode attempt pipeline (`run_direct_attempt` in `services/run_direct.py`). Must not import config or engine. Import order inside the package: leaves (`credentials.py`, `models.py`, `mutation_git.py`) → `base.py` → `cli_mutation.py` → provider modules (`copilot.py`) → `registry.py`/`factory.py`; no module imports one to its right (a cycle fails `basedpyright`, `typeCheckingMode = "recommended"` in `pyproject.toml`).
- **Git** (`core/git/`): `GitRunner`, `parse_worktree_porcelain`, `GitDiffParser`/`validate_patch_text` (`patch.py`), `PatchApplyResult`. Must not import any other `core/` package.
- **Engine** (`engine/`): Process-level run persistence, session ID minting (`RunRequest`), DB run records, canonical run execution state (`state_store.py`), the state-driven run coordinator (`coordinator.py`), paused-run validation (`loader.py`), the run session lifecycle (`session.py`), tree/row projector for `run.json` (`projection.py`) and its writer (`writer.py`), worktree/session infrastructure (`context.py`, `workspace.py`), per-step execution (`step_coordinator.py`), loop policy, events, and structural state validation (`loop_policy.py`, `loop_events.py`, `state_validation.py`), the `run.log` timeline appender (`run_log.py`), observer dispatch (`notify.py`), failure-policy resolution (`failure.py`), shared run models (`models.py`, including `BlueprintRunResult`), run/resume services (`BlueprintRunService`, `BlueprintResumeService`). May import `common/` and any `core/` package. Must not import cli.
- **Executors** (`engine/executors/`): Single-step execution (`StepExecution` in `step_executor.py`), assertions evaluation (`assertions/`), execution metadata (`metadata.py`), condition evaluation (`conditions.py`), the `type: internal` command registry and `artifacts.upload`/`artifacts.download` handlers (`internal_dispatch.py`), agent step dispatch (`agent_step.py`, including `build_agent_step_runner`), and execution models (`models.py`). Must not import engine modules outside `executors/`, or cli.
- **Catalog** (`core/catalog/`): Disk-only, multi-tier (REPO/USER/GLOBAL/PACKAGED) template scanning and indexing via per-tier `index.json` caches, packaged seeds under `templates/`. Owns authored definitions (`core/catalog/definitions/`: `StepDefinition`, `LoopStepBlock`, `BlueprintDefinition`, condition syntax), the `Blueprint` handle (`blueprint.py`), step resolution (`services/resolve_step.py`), and definition exceptions (`exceptions.py`). Must not import agents, sessions/logs, sessions/history, engine, or cli.
- **History** (`core/sessions/history/`): `History` entrypoint (`history.py`), result models (`HistoryListResult`, `HistoryShowResult`, `ReconciliationResult`), stale-run reconciliation (`services/reconcile.py`: `reconcile_stale_runs`, `is_run_stale`). UI formatters reside in `cli/ui/formatters/history/`. Must not import engine or cli.
- **Logs** (`core/sessions/logs/`): `Logs` entrypoint (`logs.py`), result models (`LogsShowResult`), `services/read.py` reading `run.log` and per-attempt step captures, the `run.log` timeline event model (`RunLogEvent` in `models.py`; the engine appends events via `engine/run_log.py`). UI formatters reside in `cli/ui/formatters/logs/`.
- **Diff** (`core/sessions/diff/`): `Diff` entrypoint (`diff.py`), session diff resolution, artifact loading, result models (`DiffResult`). UI formatters reside in `cli/ui/formatters/diff/`.
- **Status** (`core/status/`): Workspace health and runtime telemetry collection (`collect_status`), result models (`DovoStatusResult`), warning aggregation.
- **Artifacts** (`core/artifacts/`): Session artifact publishing (`publish_artifact`), listing, checksum-verified downloading, and expiry-based pruning (`Artifacts` entrypoint, `services/upload.py`, `services/download.py`, `services/prune.py`). The `type: internal` `artifacts.upload`/`artifacts.download` step handlers live in `engine/executors/internal_dispatch.py`; `engine/step_coordinator.py` also calls `publish_artifact` for the declarative `artifacts:` block auto-publish. Must not import engine.
- **Diagnostics** (`core/diagnostics/`): Diagnostic check registry (`CheckRegistry`), execution runner (`DiagnosticRunner`), entrypoint coordinator (`Diagnostics`), check protocol (`DiagnosticCheck`), and result models (`DiagnosticCheckResult`, `DiagnosticsReport`).
- **Worktree** (`core/worktree/`): Isolated git worktree checkout creation, deletion, listing, show, prune, and patch application (`Worktree` facade, `services/lifecycle.py`). Owns worktree lifecycle policy that `engine/` consumes.
- **Project** (`core/project/`): Stable project identity model (`ProjectIdentity`) with generation and persistence services.
- **Shared core infra**: `config/`, `db/`, `git/`, `bootstrap/`.

### Package boundaries (import direction)

Dependencies flow one way down the stack; do not import upward:

```
common/  ->  core/  ->  engine/  ->  cli/
core/project/  ->  core/{db,git,worktree,catalog,inputs,sessions/diff,status,artifacts}/  ->  core/agents/  ->  core/diagnostics/  ->  core/sessions/logs/  ->  core/sessions/history/
```

- `common/` never depends on `core/` or `cli/`.
- `core/project/` depends only on `common/`; `core/db/`, `core/sessions/diff/`, `engine/`, `core/worktree/`, `core/diagnostics/`, `core/sessions/history/`, and `core/sessions/logs/` may resolve project identity via `core/project/services/storage`.
- `core/` and `common/` never import `cli/` or `rich`. All terminal rendering is driven through `ui_dispatcher.dispatch(result)`.
- `core/` never imports `engine/`.
- `core/inputs/` must not import `catalog` or `agents`.
- `core/git/` imports no other `core/` package.
- `core/agents/` may use `git/`; must not import `config/` or `engine/`.
- `core/config/validate.py` may import `core/agents/registry`.
- `core/sessions/logs/` may use `db/`; must not import `engine/`, `sessions/history/`, `sessions/diff/`, or `cli/`.
- `core/sessions/diff/` must not import `sessions/history/` or `sessions/logs/`.
- `core/artifacts/` imports nothing from `engine/`.
- `core/catalog/` must not import `agents/`, `sessions/logs/`, `sessions/history/`, `engine/`, or `cli/`.
- `engine/` may import `common/` and any `core/` package; must not import `cli/`.
- `core/sessions/history/` may use `sessions/logs/` (through its public exports only) and `db/`; it must not import `engine/`, `cli/`, or `sessions/diff/`.
- `core/sessions/__init__.py` exports nothing; import from `dovo.core.sessions.{diff,history,logs}`.
- `cli/` may import `engine/`, `core/` and `common/`; lower layers never import `cli/`.
- CLI commands never render directly or import formatters; they emit results through `ui_dispatcher.dispatch(result)`.
- `cli/ui/` must not originate a domain fact. All domain facts, outcomes, warnings, and remediations originate in `core/` or `common/`; `cli/ui/` only derives presentation views.

## Adding a new command

**Relevant sources:** `src/dovo/cli/`, `src/dovo/cli/ui/`, `src/dovo/cli/cli.py`

1. Create `src/dovo/cli/<name>/` with `app.py` and `commands/<action>.py` (or `commands/root.py`).
2. Add formatters in `src/dovo/cli/ui/formatters/<name>/<model>.py` implementing `transform()` and `to_rich()`. Presentation view models reside in `src/dovo/cli/ui/formatters/<name>/<name>_views.py` (or `<name>_view.py` for single-formatter domains) for formatters that derive values. Expose registration in `src/dovo/cli/ui/formatters/<name>/__init__.py`.
3. Wire command logic directly to underlying domain services or facades (e.g. `BlueprintRunService`, `History`), dispatching results via `ui_dispatcher.dispatch(result)`. Keep CLI packages free of business logic, DB queries, or direct filesystem scans.
4. Register the command in [src/dovo/cli/cli.py](../../src/dovo/cli/cli.py).
5. Add tests under `tests/cli/<name>/`.

## Adding a new catalog-backed domain

1. **Models**: `<X>Definition` in `core/catalog/definitions/<x>.py`.
2. **Exceptions**: `<X>LoadError` / `<X>ValidationError` subclassing definition errors in `core/catalog/exceptions.py`.
3. **Handle**: `core/catalog/<x>.py` -> `Catalog.get(..., item_type=..., definition_cls=...)`.
4. **Execution**: If executing steps, run them through `Engine.run` (via a run service); do not add a separate step loop.
5. **CLI**: Thin `commands/root.py`, UI formatters in `cli/ui/formatters/<x>/`, plain-text formatters in `core/<x>/services/renderer.py` if needed for non-interactive diagnostics.

## Adding a new agent provider

**Relevant sources:** `src/dovo/core/agents/`, `src/dovo/core/config/models.py`

1. Add provider token to `AgentProvider` in `core/config/models.py` if not already present.
2. Use the direct-mutation pattern (provider CLI/SDK directly edits files in the worktree — `copilot`): subclass `CliDirectMutationAdapter` (`core/agents/cli_mutation.py`) and implement `_provider_name`, `_default_run`, and `_provider_spec`; `_preflight` is optional for provider-specific checks that run after the descriptor-driven credential check. A non-direct provider subclasses `BaseAgentProvider` and implements `_invoke` and `_provider_spec`; it never overrides `invoke`, which runs the credential check before delegating to `_invoke`.
3. Resolve secrets via module-level `resolve_<provider>_api_key()` from environment variables (never from `config.json`).
4. Declare the `ProviderSpec` in the provider module (as `copilot.py` does for `COPILOT_PROVIDER_SPEC`), import it in `core/agents/registry.py`, and add it to `PROVIDERS`; also add the token to the `agent.provider` enum in `schemas/v1/config.json`.
5. Add tests under `tests/core/agents/test_<provider>.py` with fake execution functions or transports.

## Secrets handling

**Relevant sources:** `src/dovo/core/agents/`

- API keys are resolved from the environment at call time from the descriptor's `credential_envs` via `resolve_credential`.
- Secrets are never accepted as `config.json` fields, never persisted to the centralized database, and never passed into prompt builders.

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
  worktrees/                  # git worktree checkouts
```

`sessions/`, `artifacts/`, `tmp/`, and `logs/` are no longer created locally under `.dovo/`; that project-scoped runtime state resolves under the global `DOVO_HOME` storage root instead.

### Path ownership: `RepositoryPaths` / `WorkspacePaths`

**Relevant sources:** `src/dovo/common/filesystem/models.py`, `src/dovo/core/project/services/storage.py`

- `RepositoryPaths` (`common/filesystem/models.py`) owns every repo-local path under a resolved root: `root_dir`, `dovo_dir`, `config_file`, `catalog_dir`/`catalog_steps_dir`/`catalog_blueprints_dir`, `worktrees_dir`, `lock_file`, `gitignore_file`. Built via `RepositoryPaths.from_root(root)`.
- `WorkspacePaths` (same module) extends `RepositoryPaths` with the project-scoped, identity-dependent locations: `catalog_templates_dir`, `global_paths`, `database_file`, `project_id`, `runtime_root`, `logs_dir`, `sessions_dir`, `artifacts_dir`, `tmp_dir`, plus `session_dir(id)`/`worktree_dir(id)`/`catalog_dir_for(tier)` helpers. Built via `resolve_workspace_paths(repository_paths, global_paths)` in `core/project/services/storage.py`, which resolves `project_id` from `project.json` when present.
- `CliContext.build()` (`cli/context.py`) resolves one `WorkspacePaths` snapshot per CLI invocation; CLI handlers construct their path-aware facades from that snapshot.

### Centralized SQLite database

**Relevant sources:** `src/dovo/core/db/`

- A single SQLite database at `<global_root>/data/dovo.db` (resolved by `resolve_db_path` in `core/db/connection.py`, under `DOVO_HOME` or `~/.dovo` by default) backs every project, migrated by `init_database`.
- `runs`, `worktrees`, and `costs` rows carry `project_id` and are scoped to it by every repository query, so multiple projects share the physical file without colliding. The catalog is disk-only and has no table here (see [Catalog](#layers) above).
- Repositories: `WorktreesRepository`, `RunsRepository`, `CostsRepository` accessed via `DovoDb` facade.
- Construct repositories/facades once per command invocation rather than per query.

## Worktrees (core)

**Relevant sources:** `src/dovo/core/worktree/`, `src/dovo/core/worktree/facade.py`

- Handled by `Worktree` facade (`core/worktree/facade.py`) and `core/worktree/services/lifecycle.py`.
- On-disk location: `.dovo/worktrees/<session_id>/`, branch `dovo/<id>`.
- Operations: `create`, `list`, `show`, `delete`, `prune`, `apply`, `diff`.

## Packaged resources

Schemas (`schemas/v1/`) and catalog templates (`core/catalog/templates/`) ship inside the package and are loaded via `importlib.resources` at runtime.
