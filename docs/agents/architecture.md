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
  catalog/                           Blueprint/step template discovery, disk-only multi-tier indexing, seeding, and inventory
  blueprint/                         Unified task and workflow document loading and inspection
  diff/                              Session unified diff computation and artifact retrieval
  status/                            Workspace health diagnostics and telemetry collection
  artifacts/                         Session artifact publishing, listing, downloading, and pruning
  doctor/                            Diagnostic check registry, execution runner, and health validation engine
  history/                           Execution run queries and history presentation
  logs/                              Persisted session log reading (run.log timeline, per-attempt step captures)
  step/                              Single-step execution, assertions evaluation, and step-local failure recovery
  runtime/                           In-process step-loop orchestration (run_steps), failure prompter, and pause checkpoints
  engine/                            Process facade (Engine), DB run persistence, session minting, and run/resume services
  agents/                            AI agent adapter protocol and provider integrations (local, ollama, cursor, gemini, copilot)
  patch/                             Unified-diff parsing and validation

src/worktree/common/                 Shared foundational utilities (never imports core/ or cli/)
  filesystem/                        Atomic file operations, path helpers, safe YAML I/O
  schema_validation.py               JSON Schema Draft 2020-12 validation wrapper
  lock.py, process.py, utils.py      Cross-process advisory locks, subprocess helpers, and console formatters

src/worktree/schemas/v1/             Packaged, versioned JSON Schemas (config.json, project.json, workflow.json)
```

- Default for **new** domain code: `models.py` + `services/<verb>.py`. Do not extend the flat `config/` / `db/` pattern to new domains.
- Single-step execution: `core/step/` (`runner.py`).
- Multi-step orchestration: `core/runtime/` (`run.py` -> `run_steps`).
- Process facade: `core/engine/` (`Engine.run` / `Engine.resume`, `BlueprintRunService` / `BlueprintResumeService`).

### Domain ownership

**Relevant sources:** `src/worktree/core/`

- **Inputs** (`core/inputs/`): `ParameterInput`, CLI flag resolution, `${{ inputs.* }}` placeholder interpolation. Must not import step, runtime, agents, or patch.
- **Step** (`core/step/`): `StepDefinition`, `StepAssert` / assertions, `StepExecution`, step-local failure recovery. Must not import runtime.
- **Agents** (`core/agents/`): Adapter protocol (`AgentAdapter`), provider implementations (`local`, `ollama`, `cursor`, `gemini`, `copilot`), failure payload models. Must not import step or runtime.
- **Patch** (`core/patch/`): Unified-diff parsing and validation. Must not import agents, step, or runtime.
- **Blueprint** (`core/blueprint/`): Unified task/workflow document handle (`Blueprint`), catalog/path loader, input declaration schema. Must not import runtime, engine, or cli.
- **Runtime** (`core/runtime/`): Step-loop execution (`run_steps`), `RunContext` / `RunObserver` / `RunOutcome`, failure orchestration (abort / continue / `prompt_user`), and pause checkpoint persistence. Runtime must not import cli.
- **Engine** (`core/engine/`): Process-level run persistence, session ID minting (`RunRequest`), DB run records, run/resume services (`BlueprintRunService`, `BlueprintResumeService`, `reconcile_stale_runs`). Must not import cli.
- **Catalog** (`core/catalog/`): Disk-only, multi-tier (REPO/USER/GLOBAL/PACKAGED) template scanning and indexing via per-tier `index.json` caches, packaged seeds under `templates/`.
- **History** (`core/history/`): `History` entrypoint (`history.py`), result models (`HistoryListResult`, `HistoryShowResult`). UI formatters reside in `cli/ui/formatters/history/`.
- **Logs** (`core/logs/`): `Logs` entrypoint (`logs.py`), result models (`LogsShowResult`), `services/read.py` reading `run.log` and per-attempt step captures. `RunLogEvent` is produced by `core/runtime/`. UI formatters reside in `cli/ui/formatters/logs/`.
- **Diff** (`core/diff/`): `DiffService`, session diff resolution, artifact loading, result models (`DiffResult`). UI formatters reside in `cli/ui/formatters/diff/`.
- **Status** (`core/status/`): Workspace health and runtime telemetry collection (`collect_status`), result models (`WorktreeStatusResult`), warning aggregation.
- **Artifacts** (`core/artifacts/`): Session artifact publishing (`publish_artifact`), listing, checksum-verified downloading, and expiry-based pruning (`Artifacts` entrypoint, `services/upload.py`, `services/download.py`, `services/prune.py`). Consumed by the `type: internal` `artifacts.upload`/`artifacts.download` step handlers and by `core/runtime/`'s declarative `artifacts:` block auto-publish.
- **Doctor** (`core/doctor/`): Diagnostic check registry (`CheckRegistry`), execution runner (`DiagnosticRunner`), entrypoint coordinator (`Doctor`), check protocol (`DiagnosticCheck`), and result models (`DiagnosticCheckResult`, `DoctorReport`).
- **Sandbox** (`core/sandbox/`): Isolated git worktree checkout creation, deletion, listing, show, prune, and patch application (`Sandbox` facade, `services/lifecycle.py`).
- **Project** (`core/project/`): Stable project identity model (`ProjectIdentity`) with generation and persistence services.
- **Shared core infra**: `config/`, `db/`, `git/`, `bootstrap/`.

### Package boundaries (import direction)

Dependencies flow one way down the stack; do not import upward:

```
common/  ->  core/project/  ->  core/{db,git,sandbox,catalog,inputs,patch,diff,status,artifacts}/  ->  core/agents/  ->  core/doctor/  ->  core/step/  ->  {core/runtime/, core/blueprint/}  ->  core/engine/  ->  {core/history/, core/logs/}  ->  cli/
```

- `common/` never depends on `core/` or `cli/`.
- `core/project/` depends only on `common/`; `core/db/`, `core/diff/`, `core/engine/`, `core/sandbox/`, `core/doctor/`, `core/runtime/`, `core/history/`, and `core/logs/` may resolve project identity via `core/project/services/storage`.
- `core/` and `common/` never import `cli/` or `rich`. All terminal rendering is driven through `ui_dispatcher.dispatch(result)`.
- `core/inputs/` must not import `step`, `runtime`, `agents`, or `patch`.
- `core/patch/` must not import `agents`, `step`, or `runtime`.
- `core/agents/` may use `patch/` and `config/`; must not import `step` or `runtime`.
- `core/step/` must not import `runtime`.
- `core/runtime/` may use `step/`, `db/`, `sandbox/`, `project/`, `artifacts/`; must not import `blueprint/`, `engine/`, or `cli/`.
- `core/blueprint/` may use `catalog/`, `inputs/`, `step/`; must not import `runtime/`, `engine/`, or `cli/`.
- `core/engine/` may use `runtime/`, `blueprint/`, `db/`; must not import `history/`, `logs/`, or `cli/`.
- `core/history/` may use `engine/`, `logs/`, `runtime/`, `db/`; `core/logs/` may use `runtime/`, `db/`; neither imports `cli/`.
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

1. **Models**: `<X>Definition` in `core/<x>/models.py`.
2. **Exceptions**: `<X>LoadError` / `<X>ValidationError` subclassing definition errors in `core/<x>/exceptions.py`.
3. **Loader**: `core/<x>/services/loader.py` -> `Catalog.get(..., item_type=..., definition_cls=...)`.
4. **Execution**: If executing steps, build `RunContext` and delegate to `run_steps` in `core.runtime.run`.
5. **CLI**: Thin `commands/root.py`, UI formatters in `cli/ui/formatters/<x>/`, plain-text formatters in `core/<x>/services/renderer.py` if needed for non-interactive diagnostics.

## Adding a new agent provider

**Relevant sources:** `src/worktree/core/agents/`, `src/worktree/core/config/models.py`

1. Add provider token to `AgentProvider` in `core/config/models.py` if not already present.
2. Select adapter pattern:
   - **Direct-mutation** (provider CLI/SDK directly edits files in sandbox — `cursor`, `gemini`, `copilot`): Subclass `CliDirectMutationAdapter` (`core/agents/cli_mutation.py`) and implement `_preflight`, `_provider_name`, and `_default_run`.
   - **Diff-returning** (provider returns diff text — `local`, `ollama`): Implement `AgentAdapter.propose_fix` directly (`core/agents/base.py`).
3. Resolve secrets via module-level `resolve_<provider>_api_key()` from environment variables (never from `config.json`).
4. Register in `get_agent_adapter` (`core/agents/factory.py`).
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

`sessions/`, `artifacts/`, `tmp/`, and `logs/` are no longer created locally under `.worktree/`; that project-scoped runtime state resolves under the global `WORKTREE_HOME` storage root instead (see [`resolve_project_filesystem_paths`](../../src/worktree/core/project/services/storage.py)).

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
