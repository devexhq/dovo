# Architecture

Structural map for agents. **File placement rules** (where models vs services live) are in
[code-conventions.md](code-conventions.md#core-package-layout).
User-facing command behavior lives under [docs/cli/](../cli/). Entity shapes and schemas live in [schemas.md](schemas.md).

## Layers

**Relevant sources:** `src/worktree/cli/`, `src/worktree/core/`, `src/worktree/common/`, `src/worktree/schemas/`.

```
src/worktree/cli/                    Typer CLI entrypoint and subcommand wrappers (no domain logic)
  cli.py                             Application definition, global options, top-level exception handling
  <name>/                            Subcommand packages (blueprint, config, diff, history, init, logs, resume, run, sandbox, status, step)

src/worktree/core/                   Domain business logic and orchestration (no Typer imports)
  bootstrap/                         Workspace directory structure initialization and repair
  git/                               Low-level Git CLI subprocess invocation and plumbing
  sandbox/                           Isolated git worktree sandbox lifecycle (create, delete, list, prune, apply, diff)
  config/                            Workspace configuration loading, validation, generation, and mutation
  project/                           Stable project identity model, generation, and persistence services
  db/                                SQLite persistence, connection management, Alembic migrations, and repositories
  inputs/                            Parameter input declaration, CLI flag resolution, and placeholder interpolation
  catalog/                           Blueprint/step template discovery, disk-only multi-tier indexing, seeding, inventory, and the authored blueprint/step/condition definitions with their resolution services
  blueprint/                         Blueprint run result model
  diff/                              Session unified diff computation and artifact retrieval
  status/                            Workspace health diagnostics and telemetry collection
  artifacts/                         Session artifact publishing, listing, downloading, and pruning
  doctor/                            Diagnostic check registry, execution runner, and health validation engine
  history/                           Execution run queries and history presentation
  logs/                              Persisted session logs (run.log timeline events and appender, per-attempt step captures)
  step/                              Single-step execution, assertions evaluation, and step-local failure recovery
  engine/                            Process facade (Engine), state-driven run coordinator, session lifecycle, and run/resume services
  agents/                            AI agent provider base class, descriptor registry, provider integrations (local, ollama, cursor, gemini, copilot), and the direct-mode attempt pipeline
  patch/                             Unified-diff parsing and validation

src/worktree/common/                 Shared foundational utilities (never imports core/ or cli/)
  filesystem/                        Atomic file operations, path helpers, safe YAML I/O
  schema_validation.py               JSON Schema Draft 2020-12 validation wrapper
  lock.py, process.py, utils.py      Cross-process advisory locks, subprocess helpers, and console formatters

src/worktree/schemas/v1/             Packaged, versioned JSON Schemas (config.json, project.json, workflow.json)
```

- Default for **new** domain code: `models.py` + `services/<verb>.py`. Do not extend the flat `config/` / `db/` pattern to new domains.
- Single-step execution: `core/step/` (`runner.py`).
- Multi-step orchestration: `core/engine/` (`RunCoordinator` in `coordinator.py`, driven by `drive_run` in `session.py`).
- Process facade: `core/engine/` (`Engine.run` / `Engine.resume`, `BlueprintRunService` / `BlueprintResumeService`).

### Domain ownership

**Relevant sources:** `src/worktree/core/`

- **Inputs** (`core/inputs/`): `ParameterInput`, CLI flag resolution, `${{ inputs.* }}` placeholder interpolation. Must not import catalog, step, agents, or patch.
- **Step** (`core/step/`): assertions evaluation, `StepExecution`, step-local failure recovery. Must not import engine.
- **Agents** (`core/agents/`): Provider base class (`BaseAgentProvider`), `ProviderSpec` registry (`PROVIDERS`), provider implementations (`local`, `ollama`, `cursor`, `gemini`, `copilot`), failure payload models, the direct-mode attempt pipeline (`run_direct_attempt` in `services/run_direct.py`). Must not import config, step, or engine.
- **Patch** (`core/patch/`): Unified-diff parsing and validation. Must not import agents or step.
- **Blueprint** (`core/blueprint/`): Run result model (`BlueprintRunResult`). Must not import engine or cli.
- **Engine** (`core/engine/`): Process-level run persistence, session ID minting (`RunRequest`), DB run records, canonical run execution state (`state_store.py`), the state-driven run coordinator (`coordinator.py`), paused-run validation (`loader.py`), the run session lifecycle (`session.py`), tree/row projector for `run.json` (`projection.py`) and its writer (`writer.py`), sandbox/session infrastructure (`context.py`, `workspace.py`), per-step execution (`step_coordinator.py`), loop policy, events, and structural state validation (`loop_policy.py`, `loop_events.py`, `state_validation.py`), observer dispatch (`notify.py`), failure-policy resolution (`failure.py`), shared run models (`models.py`), run/resume services (`BlueprintRunService`, `BlueprintResumeService`). May use `logs/` and `history/`. Must not import cli.
- **Catalog** (`core/catalog/`): Disk-only, multi-tier (REPO/USER/GLOBAL/PACKAGED) template scanning and indexing via per-tier `index.json` caches, packaged seeds under `templates/`. Owns authored definitions (`core/catalog/definitions/`: `StepDefinition`, `LoopStepBlock`, `BlueprintDefinition`, condition syntax), the `Blueprint` handle (`blueprint.py`), step resolution (`services/resolve_step.py`), and definition exceptions (`exceptions.py`). Must not import agents, step, engine, blueprint, logs, history, or cli.
- **History** (`core/history/`): `History` entrypoint (`history.py`), result models (`HistoryListResult`, `HistoryShowResult`, `ReconciliationResult`), stale-run reconciliation (`services/reconcile.py`: `reconcile_stale_runs`, `is_run_stale`). UI formatters reside in `cli/ui/formatters/history/`. Must not import engine or cli.
- **Logs** (`core/logs/`): `Logs` entrypoint (`logs.py`), result models (`LogsShowResult`), `services/read.py` reading `run.log` and per-attempt step captures, `services/write.py` appending `run.log` timeline events (`RunLogEvent` in `models.py`). UI formatters reside in `cli/ui/formatters/logs/`.
- **Diff** (`core/diff/`): `DiffService`, session diff resolution, artifact loading, result models (`DiffResult`). UI formatters reside in `cli/ui/formatters/diff/`.
- **Status** (`core/status/`): Workspace health and runtime telemetry collection (`collect_status`), result models (`WorktreeStatusResult`), warning aggregation.
- **Artifacts** (`core/artifacts/`): Session artifact publishing (`publish_artifact`), listing, checksum-verified downloading, and expiry-based pruning (`Artifacts` entrypoint, `services/upload.py`, `services/download.py`, `services/prune.py`). Consumed by the `type: internal` `artifacts.upload`/`artifacts.download` step handlers and by `core/engine/step_coordinator.py`'s declarative `artifacts:` block auto-publish.
- **Doctor** (`core/doctor/`): Diagnostic check registry (`CheckRegistry`), execution runner (`DiagnosticRunner`), entrypoint coordinator (`Doctor`), check protocol (`DiagnosticCheck`), and result models (`DiagnosticCheckResult`, `DoctorReport`).
- **Sandbox** (`core/sandbox/`): Isolated git worktree checkout creation, deletion, listing, show, prune, and patch application (`Sandbox` facade, `services/lifecycle.py`). Owns sandbox lifecycle policy that `core/engine` consumes.
- **Project** (`core/project/`): Stable project identity model (`ProjectIdentity`) with generation and persistence services.
- **Shared core infra**: `config/`, `db/`, `git/`, `bootstrap/`.

### Package boundaries (import direction)

Dependencies flow one way down the stack; do not import upward:

```
common/  ->  core/project/  ->  core/{db,git,sandbox,catalog,inputs,patch,diff,status,artifacts}/  ->  core/agents/  ->  core/doctor/  ->  core/step/  ->  {core/logs/, core/blueprint/}  ->  core/history/  ->  core/engine/  ->  cli/
```

- `common/` never depends on `core/` or `cli/`.
- `core/project/` depends only on `common/`; `core/db/`, `core/diff/`, `core/engine/`, `core/sandbox/`, `core/doctor/`, `core/history/`, and `core/logs/` may resolve project identity via `core/project/services/storage`.
- `core/` and `common/` never import `cli/` or `rich`. All terminal rendering is driven through `ui_dispatcher.dispatch(result)`.
- `core/inputs/` must not import `catalog`, `step`, `agents`, or `patch`.
- `core/patch/` must not import `agents` or `step`.
- `core/agents/` may use `patch/` and `git/`; must not import `config/`, `step/`, or `engine/`.
- `core/config/validate.py` may import `core/agents/registry`.
- `core/step/` must not import `engine/`.
- `core/logs/` may use `db/`; must not import `blueprint/`, `engine/`, `history/`, or `cli/`.
- `core/blueprint/` holds `BlueprintRunResult` only; must not import `engine/` or `cli/`.
- `core/catalog/` must not import `agents/`, `step/`, `engine/`, `blueprint/`, `logs/`, `history/`, or `cli/`.
- `core/engine/` may use `logs/`, `history/`, `blueprint/`, `catalog/`, `step/`, `db/`, `git/`, `sandbox/`, `project/`, `artifacts/`; must not import `cli/`.
- `core/history/` may use `logs/`, `db/`; it must not import `engine/` or `cli/`.
- `cli/` may import `core/` and `common/`; lower layers never import `cli/`.
- CLI commands never render directly or import formatters; they emit results through `ui_dispatcher.dispatch(result)`.
- `cli/ui/` must not originate a domain fact. All domain facts, outcomes, warnings, and remediations originate in `core/` or `common/`; `cli/ui/` only derives presentation views.

## Adding a new command

**Relevant sources:** `src/worktree/cli/`, `src/worktree/cli/ui/`, `src/worktree/cli/cli.py`

1. Create `src/worktree/cli/<name>/` with `app.py` and `commands/<action>.py` (or `commands/root.py`).
2. Add formatters in `src/worktree/cli/ui/formatters/<name>/<model>.py` implementing `transform()` and `to_rich()`. Presentation view models reside in `src/worktree/cli/ui/formatters/<name>/<name>_views.py` (or `<name>_view.py` for single-formatter domains) for formatters that derive values. Expose registration in `src/worktree/cli/ui/formatters/<name>/__init__.py`.
3. Wire command logic directly to underlying domain services or facades (e.g. `BlueprintRunService`, `History`), dispatching results via `ui_dispatcher.dispatch(result)`. Keep CLI packages free of business logic, DB queries, or direct filesystem scans.
4. Register the command in [src/worktree/cli/cli.py](../../src/worktree/cli/cli.py).
5. Add tests under `tests/cli/<name>/`.

## Adding a new catalog-backed domain

1. **Models**: `<X>Definition` in `core/catalog/definitions/<x>.py`.
2. **Exceptions**: `<X>LoadError` / `<X>ValidationError` subclassing definition errors in `core/catalog/exceptions.py`.
3. **Handle**: `core/catalog/<x>.py` -> `Catalog.get(..., item_type=..., definition_cls=...)`.
4. **Execution**: If executing steps, run them through `Engine.run` (via a run service); do not add a separate step loop.
5. **CLI**: Thin `commands/root.py`, UI formatters in `cli/ui/formatters/<x>/`, plain-text formatters in `core/<x>/services/renderer.py` if needed for non-interactive diagnostics.

## Adding a new agent provider

**Relevant sources:** `src/worktree/core/agents/`, `src/worktree/core/config/models.py`

1. Add provider token to `AgentProvider` in `core/config/models.py` if not already present.
2. Select adapter pattern:
   - **Direct-mutation** (provider CLI/SDK directly edits files in sandbox — `cursor`, `gemini`, `copilot`): Subclass `CliDirectMutationAdapter` (`core/agents/cli_mutation.py`) and implement `_preflight`, `_provider_name`, and `_default_run`.
   - **Diff-returning** (provider returns diff text — `local`, `ollama`): Implement `BaseAgentProvider.propose_fix` directly (`core/agents/base.py`).
3. Resolve secrets via module-level `resolve_<provider>_api_key()` from environment variables (never from `config.json`).
4. Declare a `ProviderSpec` for it in `core/agents/registry.py` and add it to `PROVIDERS`.
5. Add tests under `tests/core/agents/test_<provider>.py` with fake execution functions or transports.

## Secrets handling

**Relevant sources:** `src/worktree/core/agents/`

- API keys (`CURSOR_API_KEY`, `GEMINI_API_KEY`, `GH_TOKEN`, `GITHUB_TOKEN`) are resolved from the environment at call time.
- Secrets are never accepted as `config.json` fields, never persisted to the centralized database, and never passed into prompt builders.

## The `.worktree/` directory

Created and repaired idempotently by [core/bootstrap](../../src/worktree/core/bootstrap/):

```
.worktree/
  .meta/bootstrap.json
  .lock                       # cross-process advisory lock
  .gitignore                  # local; ignores .meta/, .lock, sandboxes/, *.db*
  config.json                 # schemas/v1/config.json
  project.json                # schemas/v1/project.json; stable project identity
  catalog/                    # blueprints/, steps/ + seeded wt/ templates; index.json is a derived cache, never hand-edited
  sandboxes/                  # git worktree checkouts
```

`sessions/`, `artifacts/`, `tmp/`, and `logs/` are no longer created locally under `.worktree/`; that project-scoped runtime state resolves under the global `WORKTREE_HOME` storage root instead.

### Path ownership: `RepositoryPaths` / `WorkspacePaths`

**Relevant sources:** `src/worktree/common/filesystem/models.py`, `src/worktree/core/project/services/storage.py`

- `RepositoryPaths` (`common/filesystem/models.py`) owns every repo-local path under a resolved root: `root_dir`, `worktree_dir`, `config_file`, `catalog_dir`/`catalog_steps_dir`/`catalog_blueprints_dir`, `sandboxes_dir`, `lock_file`, `gitignore_file`. Built via `RepositoryPaths.from_root(root)`.
- `WorkspacePaths` (same module) extends `RepositoryPaths` with the project-scoped, identity-dependent locations: `catalog_templates_dir`, `global_paths`, `database_file`, `project_id`, `runtime_root`, `logs_dir`, `sessions_dir`, `artifacts_dir`, `tmp_dir`, plus `session_dir(id)`/`sandbox_dir(id)`/`catalog_dir_for(tier)` helpers. Built via `resolve_workspace_paths(repository_paths, global_paths)` in `core/project/services/storage.py`, which resolves `project_id` from `project.json` when present.
- `CliContext.build()` (`cli/context.py`) resolves one `WorkspacePaths` snapshot per CLI invocation; CLI handlers construct their path-aware facades from that snapshot.

### Centralized SQLite database

**Relevant sources:** `src/worktree/core/db/`

- A single SQLite database at `<global_root>/data/worktree.db` (resolved by `resolve_db_path` in `core/db/connection.py`, under `WORKTREE_HOME` or `~/.worktree` by default) backs every project, migrated by `init_database`.
- `runs`, `sandboxes`, and `costs` rows carry `project_id` and are scoped to it by every repository query, so multiple projects share the physical file without colliding. The catalog is disk-only and has no table here (see [Catalog](#layers) above).
- Repositories: `SandboxesRepository`, `RunsRepository`, `CostsRepository` accessed via `WorktreeDb` facade.
- Construct repositories/facades once per command invocation rather than per query.

## Sandboxes (core)

**Relevant sources:** `src/worktree/core/sandbox/`, `src/worktree/core/sandbox/facade.py`

- Handled by `Sandbox` facade (`core/sandbox/facade.py`) and `core/sandbox/services/lifecycle.py`.
- On-disk location: `.worktree/sandboxes/<session_id>/`, branch `worktree/sandbox-<id>`.
- Operations: `create`, `list`, `show`, `delete`, `prune`, `apply`, `diff`.

## Packaged resources

Schemas (`schemas/v1/`) and catalog templates (`core/catalog/templates/`) ship inside the package and are loaded via `importlib.resources` at runtime.
