# Schemas and Entities

Comprehensive reference for the shape of entities across the Dovo CLI codebase: exceptions, DTOs, domain facades, CLI commands, and JSON/YAML schemas.

---

## 1. Exception Hierarchy and Domain Errors

**Relevant sources:**
- `src/dovo/common/exceptions.py`
- `src/dovo/common/filesystem/exceptions.py`
- `src/dovo/common/lock.py`
- `src/dovo/core/*/exceptions.py`

### Base Definition Exceptions
- `DefinitionError` (`common/exceptions.py`): Base exception for catalog-backed domain definitions.
- `DefinitionNotFoundError` (`common/exceptions.py`): Definition file or identifier not found.
- `DefinitionLoadError` (`common/exceptions.py`): YAML syntax or parse failure when reading definition.
- `DefinitionValidationError` (`common/exceptions.py`): Schema or Pydantic validation failure.

### Domain Exceptions
- **Catalog** (`core/catalog/exceptions.py`):
  - `BlueprintNotFoundError` (subclasses `DefinitionNotFoundError`)
  - `BlueprintLoadError` (subclasses `DefinitionLoadError`)
  - `BlueprintValidationError` (subclasses `DefinitionValidationError`)
  - `StepNotFoundError` (subclasses `DefinitionNotFoundError`)
  - `StepValidationError` (subclasses `DefinitionValidationError`)
  - `CatalogError` (subclasses `DefinitionError`): Base catalog exception.
  - `CatalogFileNotFoundError`: Specified catalog resource not on disk.
  - `CatalogYamlError`: YAML syntax error when parsing catalog item.
  - `CatalogWriteError`: File write or permission failure during catalog mutation.
  - `CatalogProtectionError`: Attempted delete/mutate of a protected bundled `dovo/` template.
  - `CatalogTierDeleteError`: Attempted delete of a catalog item resolved from a non-REPO tier.
- **Config** (`core/config/exceptions.py`):
  - `ConfigLoadError`: Fatal configuration loading failure.
  - `ConfigTierValidationError`: A hierarchical config tier file is unreadable, malformed, or fails `DovoConfig` validation; carries `tier`, `path`, and `details`.
- **Engine** (`engine/exceptions.py`):
  - `EngineError`: Base process engine error.
  - `EngineRuntimeError`: Execution runtime error.
  - `EngineInputError`: Input resolution failure before run creation.
  - `EngineResumeError`: Incompatible or invalid run state during resume.
  - `EngineSnapshotMissingError`: A snapshot file referenced by the run's execution-state manifest is missing from disk at resume time.
- **Git** (`core/git/exceptions.py`):
  - `GitError`: Base Git failure.
  - `GitCommandError`: Non-zero exit code from git subprocess.
  - `GitNotFoundError`: Binary missing or repository root not found.
  - `GitPlumbingTimeoutError`: Git plumbing operation timed out.
  - `MalformedDiffHeader`: Invalid unified diff format.
- **Worktree** (`core/worktree/exceptions.py`):
  - `WorktreeError`: Base worktree failure.
  - `WorktreeConfigError`: Invalid worktree configuration parameters.
  - `WorktreeCapacityError`: Active worktree limit reached.
- **Diagnostics** (`core/diagnostics/exceptions.py`):
  - `DiagnosticsError`: Base exception for diagnostics domain errors.
  - `CheckRegistrationError`: Raised when registering a check with an existing check_id.
- **Lock** (`common/lock.py`):
  - `LockTimeoutError`: Timeout acquiring `.dovo/.lock` advisory lock.
- **Global filesystem** ([`common/filesystem/exceptions.py`](../../src/dovo/common/filesystem/exceptions.py)):
  - [`InvalidGlobalRootError`](../../src/dovo/common/filesystem/exceptions.py): The selected global Dovo root contains a `.git` directory.

---

## 2. DTOs, Results, and Outcome Models

**Relevant sources:**
- `src/dovo/core/*/models.py`
- `src/dovo/common/models.py`
- `src/dovo/common/filesystem/models.py`

### Result/Outcome Pattern
All operations that can fail return a Pydantic result object subclassing `BaseResult` instead of raising:
- `status: StrEnum`: Machine-readable outcome state.
- `errors: list[str]`: Fatal error messages (inherited from `BaseResult`).
- `warnings: list[str]`: Non-fatal warning messages (inherited from `BaseResult`).
- `fixes: list[str]`: Suggested fixes or remediations (inherited from `BaseResult`).
- `error_code: str | None`: Machine-readable failure code from `dovo.common.error_codes.ErrorCode` or a plain string (inherited from `BaseResult`); `None` on success. A model validator enforces presence when `errors` or `remediations` are populated, currently short-circuited pending caller migration (issue #641).
- `ok: bool`: Property returning `True` when `not bool(self.errors)` or `status == OK`.
- Standard configuration: `model_config = {"extra": "forbid", "strict": True}` on `BaseResult`.

### Configuration Models
**Relevant sources:** `src/dovo/core/config/models.py`, `loader.py`, `validate.py`, `mutate.py`, `generator.py`.
- `DovoConfig`: Root configuration object, including `ignore_global_root_error`, defined in [`core/config/models.py`](../../src/dovo/core/config/models.py).
- Section configs: `ProjectConfig`, `WorktreeConfig`, `AgentConfig`, `HistoryConfig`, `DoctorConfig`, `PruneConfig`, `TelemetryConfig`, `ConcurrencyConfig`, `EnvironmentConfig`. `EnvironmentConfig.sensitive_variables` lists variable names only (pattern `^[A-Za-z_][A-Za-z0-9_]*$`, never values); `Config.environment` exposes the section, and the hierarchical loader unions the list across the Global, User and Repo tiers instead of replacing it. `AgentConfig.env_passthrough` (names or `PREFIX*` patterns, never values) and `AgentConfig.env_mode` (`allowlist` | `inherit`) use the default replace-across-tiers merge, so a repo list replaces a global one.
- `ConfigLoadResult`: Result of loading and validating `.dovo/config.json` (`status`, `config_path`, `raw`, `config`, `errors`, `ok`). `ConfigLoadStatus.TIER_INVALID` classifies a Global or User tier failure surfaced by `resolve_effective_config` ([`core/config/services/resolve.py`](../../src/dovo/core/config/services/resolve.py)), which `Config.load()`/`Config._loaded_config` — and therefore every `Config.<section>` accessor, `dovo config show`, and blueprint execution (`dovo run`/`dovo resume`) — route through.
- `ConfigValidationResult`: Result of semantic config validation (`status`, `config_path`, `raw`, `config`, `errors`, `warnings`, `ok`).
- `ConfigSetResult`: Result of mutating a dot-path key in config (`status`, `config_path`, `key`, `value`, `errors`, `ok`).
- `ConfigUnsetResult`: Result of removing a dot-path key from config (`status`, `config_path`, `key`, `existed`, `previous_value`, `errors`, `ok`).
- `ConfigGenerationResult`: Result of creating, repairing, or overwriting config (`created`, `skipped_existing`, `repaired`, `overwritten`, `inserted_keys`, `warnings`, `errors`, `ok`).
- [`GlobalPaths`](../../src/dovo/common/filesystem/models.py): Canonical paths for the global Dovo hierarchy.
- [`RepositoryPaths`](../../src/dovo/common/filesystem/models.py): Repo-local paths resolved from a root directory (`root_dir`, `dovo_dir`, `config_file`, `catalog_dir`, `catalog_steps_dir`, `catalog_blueprints_dir`, `worktrees_dir`, `lock_file`, `gitignore_file`). Built via `RepositoryPaths.from_root(root)`.
- [`WorkspacePaths`](../../src/dovo/common/filesystem/models.py): Extends `RepositoryPaths` with project-scoped, identity-dependent locations (`catalog_templates_dir`, `global_paths`, `database_file`, `project_id`, `runtime_root`, `logs_dir`, `sessions_dir`, `artifacts_dir`, `tmp_dir`) plus `session_dir(id)`/`worktree_dir(id)`/`catalog_dir_for(tier)` helpers. Built via `resolve_workspace_paths(repository_paths, global_paths)` ([`core/project/services/storage.py`](../../src/dovo/core/project/services/storage.py)), which loads the required `project_id` from `project.json` and raises [`WorkspaceNotInitializedError`](../../src/dovo/common/filesystem/exceptions.py) when it is missing or unusable. Every domain facade/service takes `paths: WorkspacePaths` as its single source of ambient location state — see [architecture.md](architecture.md#path-ownership-repositorypaths--workspacepaths).
- `ConfigTier`, `ConfigLayer`: Precedence-tier enum and resolved-layer DTO for hierarchical config resolution, defined in [`core/config/models.py`](../../src/dovo/core/config/models.py). [`core/config/services/hierarchical_loader.py`](../../src/dovo/core/config/services/hierarchical_loader.py) exposes `load_hierarchical_config` and `resolve_config_layers`, merging Packaged, Global, User, and Repo tiers into a validated `DovoConfig`.
- `HierarchicalConfigLoadResult` / `HierarchicalConfigLoadStatus`: Non-raising result of `load_hierarchical_config` (`status`, `tier`, `path`, `config`, `errors`, `ok`); `status` classifies which tier's file was unreadable, malformed, non-object, or failed `DovoConfig` validation.

### Project Identity Model
**Relevant source:** `src/dovo/core/project/models.py`.
- `ProjectIdentity`: Strict, extra-forbidding stable project identity. Its identifier is lowercase URL-safe text from 3 to 63 characters, optional display names must contain a non-whitespace character, and creation timestamps must be UTC. Its classified persistence DTOs and non-raising load/save behavior are defined alongside it; the operations are in `src/dovo/core/project/services/identity.py`.
- `ProjectIdentityProvisionResult` / `ProjectIdentityProvisionStatus`: Non-raising outcome of `dovo init`'s create-or-preserve-or-overwrite identity provisioning (`src/dovo/core/project/services/identity.py:provision_project_identity`).

### Blueprint & Step Models
**Relevant sources:** `src/dovo/core/catalog/definitions/`, `src/dovo/engine/executors/models.py`, `src/dovo/core/inputs/models.py`.
- `BlueprintDefinition`: Unified model for executable blueprints. See [`src/dovo/core/catalog/definitions/blueprint.py`](../../src/dovo/core/catalog/definitions/blueprint.py) for its fields; it carries no `kind` discriminator.
- `BlueprintDefaults`: Blueprint-level defaults (`on_failure`).
- `ParameterInput`: Declared parameter input (`type`, `description`, `required`, `default`, `aliases`).
- `InputResolveResult`: Result of resolving input values from CLI flags and defaults (`values`, `missing`, `errors`, `warnings`, `ok`).
- `StepDefinition`: Executable step specification (`id`, `name`, `type`, `description`, `command`, `prompt`, `script_path`, `tools`, `env`, `timeout_seconds`, `assert_`, `on_failure`, `uses`, `run`, `artifacts`). `type` is a `StepType` (`command`, `agent`, `script`, `internal`); `internal` requires a non-empty `command` naming an `INTERNAL_COMMAND_HANDLERS` registry key (`engine/executors/internal_dispatch.py`). `artifacts: list[ArtifactPublishSpec]` declares bundles auto-published via `publish_artifact` after a successful step, on both top-level and loop `do:` sub-steps; see Artifacts Models below.
- `ArtifactPublishSpec`: Declarative per-step artifact publish spec (`name`, `path`, `retention_days`).
- `InternalCommandContext`: Structured inputs passed to an in-process `type: internal` handler (`worktree_path`, `session_id`, `env`, `artifacts_dir`, `artifacts_db`); see [`src/dovo/engine/executors/models.py`](../../src/dovo/engine/executors/models.py).
- `ExecutionMetadata.session_id`: The run's session ID, threaded from `RunSettings.session_id` through `build_execution_metadata` and exposed to `type: command`/`script` steps as the `DOVO_SESSION_ID` environment variable (`engine/executors/metadata.py`).
- `StepAssert`: Verification conditions (`exit_code`, `output_contains`, `output_not_contains`, `regex_match`, `json_match`, `file_exists`, `file_not_exists`, `file_not_empty`).
- `FailurePolicy`: `StrEnum` (`abort`, `continue`, `prompt_user`, `retry`). Terminal policies exclude `retry`.
- `FailureSpec`: Normalized failure policy (`action`, `max_retries`, `backoff_ms`, `on_max_retries`).
- `LoopStepBlock`: Step container repeating `do: []` until `until` condition or `max_iterations` (`id`, `type="loop"`, `max_iterations`, `until`, `do`, `on_max_iterations`).
- `StepResult`: Step execution outcome (`step_id`, `status`, `exit_code`, `stdout`, `stderr`, `duration_seconds`, `attempts`, `error_message`, `outputs`, `ok`).
- `AssertionResult`: Assertion evaluation outcome (`passed`, `failed_conditions`, `message`).

### Run Engine Models
**Relevant sources:** `src/dovo/engine/models.py`, `src/dovo/core/sessions/models.py`.
- [`RunSettings`](../../src/dovo/engine/models.py): Settings and collaborators resolved from the session row for worktree/session setup and step coordination (`use_worktree`, `keep`, `agent` as `ResolvedAgentSettings | None`, `observer`, `inputs`, `no_tty`, `failure_prompter`, `auto_apply`, `worktree_id`, `sensitive_variables`, `paths`).
- [`RunContext`](../../src/dovo/engine/models.py): Infrastructure resources for one run's execution, including `sensitive_variables` resolved once in `SessionRunner.open` and carried to `RunSettings.sensitive_variables` and `StepExecutionContext.sensitive_variables` for secret masking; durable progress lives only in `ExecutionStateTree`.
- [`RunOutcome`](../../src/dovo/engine/models.py): Terminal run result, including the worktree and session identifiers.
- [`RunObserver`](../../src/dovo/engine/models.py), [`FailurePrompter`](../../src/dovo/engine/models.py), [`FailurePromptDecision`](../../src/dovo/engine/models.py), [`LoopPromptDecision`](../../src/dovo/engine/models.py): Caller-supplied progress hooks and failure/loop decision entrypoints. `RunObserver` callbacks: `on_run_started(steps)` and `on_run_completed(outcome)` (once per `drive_run` invocation; `on_run_started` is skipped when definitions fail to load, `on_run_completed` receives the returned outcome), `on_step_start`, `on_step_output`, `on_step_done(idx, total, step, result)`, `on_loop_start`, `on_loop_iteration_start`, `on_loop_conditions_evaluated`, `on_loop_done`, and the worktree hooks.
- [`StepAction`](../../src/dovo/engine/models.py): Orchestration action (retry, continue, abort) `RunCoordinator` applies after a terminal step failure; distinct from the user-input `FailurePromptDecision` and the persisted `NodeTransitionKind`.
- [`SessionStatus`](../../src/dovo/core/db/models.py): `StrEnum` lifecycle status of a session record.
- `RunRequest`: Facade execution parameters for `Engine.run` (`inputs`, `cli_args`, `use_worktree`, `keep`, `agent`, `session_id`, `observer`, `failure_prompter`, `no_tty`).
- [`RunCoordinator`](../../src/dovo/engine/coordinator.py) / `NodeTransitionKind`: State-driven execution of a run: selects the next non-terminal node, applies one durable transition through `SessionStateStore`, and repeats until the run completes, pauses, or fails. `Engine.run` and `Engine.resume` reach it through `drive_run` ([`session.py`](../../src/dovo/engine/session.py)).
- [`EngineLoader`](../../src/dovo/engine/loader.py): Validates a paused run's row, execution state, snapshots, and retained worktree; raises `EngineResumeError` carrying an `EngineResumeStatus`.
- [`flatten_step_results`](../../src/dovo/engine/projection.py): Projects the terminal leaf attempts of an `ExecutionStateTree` into the ordered `RunOutcome.step_results`.
- `EngineResumeStatus`: `StrEnum` (`ok`, `not_found`, `wrong_status`, `missing_worktree`, `corrupt_state`, `missing_snapshot`, `failed`).
- [`ExecutionStateTree`](../../src/dovo/engine/state_models.py): Versioned plan-and-progress document for one run (manifest plus ordered step and loop nodes). A paused leaf (top-level or loop body) keeps its failed attempt, so resume re-enters the failure prompt without re-running the step. `ExecutionLoopNode.granted_iterations` records ceiling grants; the effective ceiling is `max_iterations + granted_iterations`.
- [`SessionLifecycle`](../../src/dovo/engine/state_models.py) / [`SessionJsonPayload`](../../src/dovo/engine/state_models.py): The `session.json` projection of a run: frozen manifest, execution tree, row-derived lifecycle outcome, and results flattened from the tree. Built by [`build_session_json_payload`](../../src/dovo/engine/projection.py) and written by [`write_session_projection`](../../src/dovo/engine/writer.py).
- [`SessionStateStore`](../../src/dovo/engine/state_store.py): Builds, saves (revision compare-and-swap), and loads an `ExecutionStateTree` for one session row; returns `SessionStateWriteResult` / `SessionStateLoadResult` (`SessionStateWriteStatus` / `SessionStateLoadStatus` in [`state_models.py`](../../src/dovo/engine/state_models.py)). Does not lock; callers hold the workspace lock.
- [`RunStartConfig`](../../src/dovo/engine/models.py): Resolved run options written to the session row when a run starts.
- `DefinitionRef`: One snapshotted catalog item's resolved reference, content SHA, and resolution timestamp (`ref`, `sha`, `resolved_at`); `ref` is `"<tier>:<item_type>:<key>"`.
- `DefinitionsManifest`: The blueprint's `DefinitionRef` plus a `DefinitionRef` per transitively-resolved `uses:` step (`blueprint`, `steps`), snapshotted by `Engine.run` into `<session_dir>/definitions/` and consumed by `RunCoordinator`/`load_blueprint_from_snapshot` to execute and resume without a live catalog read.
- [`SessionLogEvent`](../../src/dovo/core/sessions/models.py) / `SessionLogEventType`: One `session.log` timeline record. `drive_run`, `RunCoordinator`, `StepCoordinator`, and `LoopEventEmitter` append one JSON line per lifecycle event to `logs_dir/<session_id>/session.log` via `append_session_log_event` ([`engine/session_log.py`](../../src/dovo/engine/session_log.py)), which stamps `ts` at write time and drops write failures silently. `event` decides which optional fields are populated. `step_start`/`step_done` events carry `step_name` (`null` when the step has none) and, for loop body steps, `loop_id` and the 1-based `iteration`; `step_done` also carries `duration_seconds`. `agent_env_filtered` carries the comma-joined `env_withheld` names for an agent step in `allowlist` mode. Loop events use `iteration` and `next_iteration`, and `loop_done` carries the loop's total iteration count in `iteration`. [`core/sessions/services/read_logs.py`](../../src/dovo/core/sessions/services/read_logs.py) reads these events back.
- [`StepCoordinator`](../../src/dovo/engine/step_coordinator.py), [`Workspace`](../../src/dovo/engine/workspace.py): Per-step attempt and failure-prompt primitives and worktree/session lifecycle used by the coordinator.
- [`LoopPolicy`](../../src/dovo/engine/loop_policy.py) / `LoopDecision` / `LoopTransitionKind`: Pure loop decisions (advance a body step, complete an iteration, repeat, terminate, or apply the `max_iterations` ceiling) that `RunCoordinator` applies durably. [`LoopEventEmitter`](../../src/dovo/engine/loop_events.py) emits the loop `session.log` events and observer callbacks; `validate_loop_structure` in [`state_validation.py`](../../src/dovo/engine/state_validation.py) rejects persisted loop state whose ids or body order differ from the run snapshot.

### Agent Provider Models
**Relevant sources:** `src/dovo/core/agents/models.py`, `src/dovo/core/agents/responses.py`, `src/dovo/core/agents/cli_mutation.py`.
- [`AgentRequest`](../../src/dovo/core/agents/models.py): Input to an agent adapter. `mode` is `direct` (authored step prompt as `instruction`, no `payload`), `fix_failure`, or `review_remediation` (both require an `AgentFailurePayload`); a blank `instruction` is rejected. `env_passthrough` and `env_mode` come from `ResolvedAgentSettings`; `env` (the interpolated agent-step `env`) and `metadata_env` (the generated `DOVO_*` map) are process-environment inputs excluded from serialized dumps. [`build_agent_env`](../../src/dovo/core/agents/environment.py) turns them into the subprocess environment.
- [`AgentStepSummary`](../../src/dovo/engine/executors/models.py): JSON object an agent step writes to stdout and that `StepResult.stdout` holds; its status maps to the step exit code through `AGENT_OUTCOME_EXIT_CODES` in [`agent_step.py`](../../src/dovo/engine/executors/agent_step.py). Built by [`execute_agent_step`](../../src/dovo/engine/executors/agent_step.py) from an `AgentAttempt`. `StepExecutionContext.agent_runner` (built by `build_agent_step_runner`) carries the run's provider settings and worktree state, and agent steps fail without an active Dovo Git worktree.
- `AgentStepRunner` / `OutputCallback`: Type aliases in [`engine/executors/models.py`](../../src/dovo/engine/executors/models.py); `AgentStepRunner` is the `(StepDefinition, Path, OutputCallback | None, ExecutionMetadata) -> StepDispatchOutcome` callable `StepExecution` invokes for agent steps, and `OutputCallback` is the `(stream, text)` output sink.
- [`AgentAttempt`](../../src/dovo/core/agents/models.py): Classified result of `run_direct_attempt` ([`core/agents/services/run_direct.py`](../../src/dovo/core/agents/services/run_direct.py)); `completed` is true only for `proposed_patch` and `no_op`.
- [`AgentResponse`](../../src/dovo/core/agents/models.py): Adapter outcome. Adapters build the timeout, provider-error, and no-op responses through the constructors in [`responses.py`](../../src/dovo/core/agents/responses.py). `fixes` carries remediation hints (the timeout response sets one); `run_direct_attempt` appends them to the attempt diagnostics as a trailing `Fix:` block.
- [`AgentInvocationContext`](../../src/dovo/core/agents/models.py): Per-attempt `invocation_id`, `scratch_path`, and `control_path` allocated by `allocate_invocation_paths` in [`scratch.py`](../../src/dovo/core/agents/scratch.py) at the step boundary. `control_path` is host-owned and never reaches prompts, environments, or grants. Carried on `AgentRequest.invocation` and `CliMutationRunRequest.invocation`, both excluded from serialized dumps; `AgentRequest.agent_scratch_path` is the only scratch path exposed to prompt construction and must equal `invocation.scratch_path` when both are set.
- [`AgentScratchResult`](../../src/dovo/core/agents/models.py): Result of allocation; keeps `invocation_id` on failure and carries `AGENT_SCRATCH_UNAVAILABLE` as its `error_code`, which surfaces as a `provider_error` step failure (exit `203`).
- `AgentResponseStatus`: `StrEnum` (`proposed_patch`, `no_op`, `unfixable`, `timeout`, `provider_error`).
- `AgentFailurePayload`: Step failure context captured for agent prompts (`step_id`, `command`, `exit_code`, `stdout`, `stderr`, `duration_seconds`, `error_message`, `files`).
- [`ResolvedAgentSettings`](../../src/dovo/core/agents/models.py): Resolved once per drive in `drive_run` from `Config(paths).load()`; a non-empty run-row override replaces only `provider`; `env_passthrough` and `env_mode` are the config values with `--env-passthrough` entries appended (de-duplicated) and `--env-mode` applied; carried to steps through the `agent_runner` closure built by `build_agent_step_runner`, not a `StepExecutionContext` field. [`AgentEnvOverrides`](../../src/dovo/core/agents/models.py) carries the CLI flags on `RunRequest.env_overrides` and `Engine.resume` and is never persisted. [`ProviderSpec`](../../src/dovo/core/agents/base.py) / `PROVIDERS` (`core/agents/registry.py`) is the single provider list, read by the factory and `validate_config_result`.
- [`CliMutationRunRequest`](../../src/dovo/core/agents/cli_mutation.py): Execution payload for direct-mutation adapters (`prompt`, `worktree_path`, `model`, `timeout_seconds`, plus the excluded `invocation` and the excluded built subprocess `env`). `AgentResponse.env_withheld` and `AgentAttempt.env_withheld` carry the names (never values) the allowlist withheld, so the step boundary can report them.
- `CliMutationOutcome`: Direct-mutation subprocess result (`success`, `exit_code`, `stdout`, `stderr`, `error_message`).

### Worktree Models
**Relevant sources:** `src/dovo/core/worktree/models.py`.
- `WorktreeSession`: Active worktree metadata (`session_id`, `worktree_path`, `target_branch`, `base_commit`, `created_at`, `status`).
- `WorktreeCreateResult`: Result of creating worktree (`status`, `session_id`, `worktree_path`, `branch_name`, `errors`, `warnings`, `ok`).
- `WorktreeDeleteResult`: Result of deleting worktree (`status`, `session_id`, `worktree_path`, `branch_deleted`, `errors`, `warnings`, `ok`).
- `WorktreePruneResult`: Result of pruning stale/orphan worktrees (`status`, `pruned_items`, `errors`, `warnings`, `ok`).
- `WorktreeListResult`, `WorktreeShowResult`, `WorktreeApplyResult`, `WorktreeDiffResult`, `WorktreeDetectionResult`.

### Artifacts Models
**Relevant sources:** `src/dovo/core/artifacts/models.py`.
- `ArtifactManifest` / `ArtifactManifestFile`: `manifest.json` contents written alongside every published artifact bundle (`name`, `session_id`, `created_at`, `expires_at`, `size_bytes`, `file_count`, `files: list[ArtifactManifestFile]`; each file entry carries `path`, `sha256`, `size_bytes`).
- `ArtifactUploadResult` / `ArtifactUploadStatus`: Result of `publish_artifact` (`ok`, `no_matching_files`, `error`); constructed only by `core/artifacts/services/upload.py` and the `type: internal` `artifacts.upload` handler — never dispatched through `ui_dispatcher`.
- `ArtifactDownloadResult` / `ArtifactDownloadStatus`: Result of `download_artifact` (`ok`, `not_found`, `checksum_mismatch`, `error`); checksum verification runs to completion before any file is copied into `--dest`.
- `ArtifactsListResult` / `ArtifactsListStatus`: Result of listing artifacts for the current project (`ok`).
- `ArtifactsPruneResult` / `ArtifactsPruneStatus` / `PrunedArtifact`: Result of `dovo artifacts prune` (`ok`, `disabled`, `locked`); `disabled` means `prune.remove_expired_artifacts` is `False` and `--force` was not passed.

### Catalog Models
**Relevant sources:** `src/dovo/core/catalog/models.py`.

The catalog is disk-only: each of the REPO, USER, and GLOBAL tiers keeps its own `index.json` cache, rebuilt wholesale from a directory walk on every lookup; PACKAGED is read directly from bundled resources. There is no SQLite-backed catalog table.
- `CatalogTier`: `StrEnum` (`packaged`, `global`, `user`, `repo`); resolution precedence is REPO, then USER, then GLOBAL, then PACKAGED.
- `CatalogItemType`: `StrEnum` (`blueprint`, `step`).
- `CatalogItemTypeDirectory`: `StrEnum` (`blueprints`, `steps`) — the on-disk subdirectory name for each item type.
- `CatalogIndexEntry`: One item's identity and location as stored in a tier's `index.json` (`sha`, `key`, `item_type`, `name`, `namespace`, `path`, `checksum`).
- `CatalogIndex`: On-disk contents of one tier root's `index.json` (`items: list[CatalogIndexEntry]`).
- `CatalogRecord`: A `CatalogIndexEntry` plus the `tier` it was resolved from — the shape returned by `Catalog.list/show/get/create/delete`.
- `SeedResult`: Template seeding outcome (`created_files`, `skipped_existing_files`, `overwritten_files`, `warnings`, `errors`, `ok`).
- `CatalogCreateResult`: Result of creating a catalog blueprint or step (`item`, `resolved_path`, `errors`, `warnings`, `fixes`, `ok`).
- `CatalogValidateResult`: Result of validating a catalog blueprint or step definition without executing it (`status`, `valid`, `target`, `resolved_path`, `item_type`, `errors`, `warnings`, `ok`); `CatalogValidateStatus`: `StrEnum` (`ok`, `invalid`, `syntax_error`, `not_found`, `unreadable`, `type_required`).

### Database SQLModel Records
**Relevant sources:** `src/dovo/core/db/models.py`.

All four tables live in one centralized SQLite database shared across projects (`resolve_db_path` in `core/db/connection.py`), and every record carries `project_id`; every `BaseRepository` query scopes on it (`core/db/repositories/base.py`). The catalog is no longer one of them — see Catalog Models above.
- `WorktreeRecord`: Persisted worktree rows in `worktrees` table.
- `SessionRecord`: Persisted blueprint session rows in `sessions` table (including the execution-state/config columns — see [`SessionRecord`](../../src/dovo/core/db/models.py); `worktree_id` is written by `RunCoordinator` when it creates or reuses a worktree session). The row (`execution_state_json`, `execution_state_revision`) is the canonical execution state; `<session-dir>/session.json` is its database-derived projection (manifest, tree, lifecycle, flattened results; see `SessionJsonPayload`), regenerated from the row on load when missing, corrupt, or differing from the row, and a newer file is rejected rather than imported. `worktree_kept` records the run's worktree-kept outcome, separate from the `keep` request.
- `CostRecord`: Persisted token and execution cost tracking in `costs` table.
- `ArtifactRecord`: Persisted artifact metadata rows in `artifacts` table (`id`, `project_id`, `session_id`, `name`, `path`, `size_bytes`, `file_count`, `created_at`, `expires_at`); unique on `(project_id, session_id, name)`, upserted by `ArtifactsRepository.create`.

### History, Diff, and Status Models
**Relevant sources:** `src/dovo/core/sessions/models.py`, `src/dovo/core/status/models.py`.
- `HistoryListResult`, `HistoryShowResult`: History query results. `HistoryShowResult.log_files`/`log_snippet` are populated only by `Session.details(include_logs=True)`; the snippet holds the last 10 `session.log` events as plain text lines.
- `LogsShowResult` / `LogsShowStatus` / `LogStreamFilter` ([`core/sessions/models.py`](../../src/dovo/core/sessions/models.py)): `dovo logs` outcome. Only one of `events` (parsed `session.log`, no `step` filter) or `lines` (raw step capture, `step` filter) is populated per call. `available_steps`/`available_attempts` are filled on `STEP_NOT_FOUND`/`ATTEMPT_NOT_FOUND`. Step identity is parsed from the capture filename `<NN>_<step_id>[_iter_<n>]_attempt_<n>.<stream>.log`; there is no sidecar index.
- `DiffResult`: Session unified-diff and artifact outcome (`status`, `diff_text`, `files_changed`, `errors`, `ok`).
- `DovoStatusResult`: Workspace health, repository status, and collected developer warnings.

### Doctor Models
**Relevant sources:** `src/dovo/core/diagnostics/models.py`, `src/dovo/core/diagnostics/services/runner.py`, `src/dovo/core/diagnostics/services/remediation.py`.
- `CheckStatus`: `StrEnum` (`ok`, `warning`, `failed`, `skipped`).
- `CheckCategory`: `StrEnum` (`git`, `config`, `filesystem`, `worktree`, `agent`, `environment`).
- `DiagnosticsContext`: Contextual environment supplied to checks (`cwd`, `config`).
- `RemediationType`: `StrEnum` classifying how a remediation is carried out.
- `Remediation`: Deterministic, copy-pasteable remediation action for a failing or warning check.
- `DiagnosticCheckResult`: Individual check outcome model defined in `src/dovo/core/diagnostics/models.py`, including its `remediations: list[Remediation]` field; `error_code` is inherited from `BaseResult`, not defined on this class.
- `DiagnosticsReport`: Aggregated execution report model defined in `src/dovo/core/diagnostics/models.py`.
- `DiagnosticCheck`: Protocol defining check identification and execution contract.
- Built-in checks (`src/dovo/core/diagnostics/checks/`, registered by `get_default_registry()` in `src/dovo/core/diagnostics/services/registry.py`):
  - `git.repo` (`GitRepoCheck`): validates the `git` binary is on `PATH` and `context.cwd` is a Git repository.
  - `config.schema` (`ConfigSchemaCheck`): validates `.dovo/config.json` exists and passes schema V1 validation.
  - `filesystem.writable` (`FilesystemWritableCheck`): probes write access across `WorkspacePaths`-declared directories.
  - `worktree.refs` (`WorktreeRefsCheck`): validates registered worktree directories against `WorktreesRepository` and Git worktree state.
  - `env.binaries` (`EnvBinariesCheck`): validates required host and active agent provider CLI binaries are on `PATH`.
  - `agent.setup` (`AgentSetupCheck`): validates the active agent provider's credential and configured model.
- Error codes:
  - `DOCTOR_CHECK_CRASH`: Diagnostic check threw an unhandled exception during execution.
  - `DOCTOR_GIT_BINARY_MISSING`: `git` executable not found on `PATH`.
  - `DOCTOR_GIT_NOT_REPO`: `context.cwd` is not a valid Git repository or worktree.
  - `DOCTOR_CONFIG_NOT_FOUND`: `.dovo/config.json` does not exist.
  - `DOCTOR_CONFIG_MALFORMED`: `.dovo/config.json` contains invalid JSON syntax.
  - `DOCTOR_CONFIG_SCHEMA_INVALID`: `.dovo/config.json` fails schema V1 validation, has a non-object root, is a directory, or is unreadable.
  - `DOCTOR_FS_UNWRITABLE`: One or more configured workspace paths rejected a probe write.
  - `DOCTOR_WORKTREE_STALE`: Stale or broken worktree references detected.
  - `DOCTOR_WORKTREE_ORPHAN`: Unregistered worktree folders found.
  - `DOCTOR_BINARY_MISSING`: Configured provider binary is missing from `PATH`.
  - `DOCTOR_AGENT_KEY_MISSING`: Required API key environment variable is missing.
  - `DOCTOR_AGENT_NO_MODEL`: Agent provider model is not configured.
- `resolve_remediations(result) -> list[Remediation]` / `format_remediation_summary(remediations) -> str` (`core/diagnostics/services/remediation.py`): deterministic mapping from `error_code`/`details` to remediation actions, and a terminal/log-friendly text renderer.
- `DoctorCheckView`, `DoctorReportView` (`src/dovo/cli/ui/formatters/doctor/doctor_views.py`): CLI presentation reshaping of `DiagnosticCheckResult`/`DiagnosticsReport` for `dovo doctor`, field for field.

---

## 3. Domain Facades

**Relevant sources:**
- `src/dovo/core/*/facade.py`
- `src/dovo/common/filesystem/facade.py`
- `src/dovo/engine/engine.py`
- `src/dovo/core/git/runner.py`

Each core domain exposes a cohesive facade class that encapsulates domain services, queries, and repositories:

| Facade | Module | Key Responsibilities & Methods |
|:---|:---|:---|
| `Bootstrap` | `core/bootstrap/facade.py` | Idempotent workspace initialization and repair (`ensure_workspace`, `initialize_workspace`). |
| `GitRunner` | `core/git/runner.py` | Low-level git CLI execution (`run`, `worktree_add`, `worktree_remove`, `worktree_list`, `diff`). |
| `Worktree` | `core/worktree/facade.py` | Dovo worktree lifecycle (`create`, `show`, `list`, `delete`, `prune`, `apply`, `diff`). |
| `Config` | `core/config/facade.py` | Config loading, validation, generation, and mutation (`load`, `validate`, `set`, `unset`, `generate`, `show`). |
| `DovoDb` | `core/db/db.py` | Central database access point (`worktrees`, `sessions`, `costs`, `artifacts` repositories). |
| `Artifacts` | `core/artifacts/artifacts.py` | Session artifact publishing, listing, downloading, and pruning (`upload`, `list`, `download`, `prune`). |
| `Inputs` | `core/inputs/facade.py` | Input flag parsing, default resolution, and placeholder interpolation (`parse_args`, `resolve`, `interpolate`). |
| `Catalog` | `core/catalog/catalog.py` | Disk-only, multi-tier template scanning, indexing, retrieval, and seeding (`list`, `show`, `get`, `create`, `delete`, `sync`, `validate`, `seed`). |
| `Blueprint` | `core/catalog/blueprint.py` | Loading a catalog blueprint document (`load`, `steps`, `inputs`, `use_worktree`, `dump`, `resolve_inputs`). |
| `Status` | `core/status/facade.py` | Workspace health and telemetry aggregation (`collect`). |
| `Session` | `core/sessions/sessions.py` | One session addressed by id (`diff`, `logs`, `details`); a session exists when its session record exists. |
| `SessionCollection` | `core/sessions/sessions.py` | The project's sessions (`get`, `list`, `latest_diff`); `list` reconciles stale sessions, `latest_diff` selects the greatest `started_at`. `Session` and `SessionCollection` take a keyword-only `sensitive_variables` that `logs`, `details` and `list` pass to the read-time redactor. |
| `Engine` | `engine/engine.py` | Process-level run persistence, session minting, execution, and resume (`run`, `resume`). |
| `Filesystem` | `common/filesystem/facade.py` | Atomic writes, safe path operations, and YAML parsing (`atomic_write_json`, `atomic_write_text`, `read_yaml`). |

---

## 4. Commands and CLI Surface

**Relevant sources:**
- `src/dovo/cli/cli.py`
- `src/dovo/cli/<name>/`

### Entrypoint and Global Options
- Application: `app = typer.Typer(cls=DovoTyperGroup, name="dovo")` in `src/dovo/cli/cli.py`.
- Global options:
  - `-p, --path`: Workspace root directory.
  - `-v, --verbose`: Verbose telemetry logging.
  - `--version`: Print CLI version and exit.

### Subcommand Structure
Each CLI command package under `src/dovo/cli/<name>/` contains:
- `app.py`: Subcommand Typer app registration.
- `commands/`: Individual command implementations (e.g. `root.py`, `commands/<action>.py`) returning core domain `*Result` models.
- `formatters.py`: UI dispatcher component formatters for core domain `*Result` models.
- `renderers.py`: Rich-based terminal presentation functions.

### Registered CLI Commands
- `dovo init`: Initialize workspace, generate `.dovo/` directory, `project.json` identity, `.gitignore`, and `config.json` (`--id`, `--display-name`, `--force`).
- `dovo status`: Show workspace health, active worktrees, and developer warnings.
- `dovo config`: Manage configuration (`show`, `set`, `validate`).
- `dovo blueprint`: Manage catalog blueprint items across all tiers (`list`/`ls`, `show`, `create`, `delete`, `validate`).
- `dovo step`: Manage catalog step items across all tiers (`list`/`ls`, `show`, `create`, `delete`, `validate`).
- `dovo run`: Execute a task or workflow blueprint.
- `dovo resume`: Resume a paused execution session.
- `dovo worktree`: Manage git worktrees (`create`, `list`, `show`, `delete`, `prune`, `apply`).
- `dovo history`: Query past session records (`history`, `history show`, `history show --logs`).
- `dovo logs <session_id>`: Show a session's `session.log` timeline, or one step's raw capture (`--step`, `--attempt`, `--stream`, `--tail`, `--format`).
- `dovo artifacts`: Publish, list, download, and prune session artifacts (`list`, `download`, `prune`; publishing is reached only through the `type: internal` `dovo/upload-artifact` catalog step or a step's declarative `artifacts:` block, not a CLI command).
- `dovo diff`: Show uncommitted or session diffs.
- `dovo doctor`: Run registered diagnostic checks and print a scannable workspace health report (`--category`, `--format`).

---

## 5. JSON and YAML Schemas

**Relevant sources:**
- `src/dovo/schemas/v1/config.json`
- `src/dovo/schemas/v1/project.json`
- `src/dovo/schemas/v1/workflow.json`
- `src/dovo/common/schema_validation.py`

### Schema Contracts
- **Config V1 (`v1/config.json`)**:
  - Validates `.dovo/config.json`.
  - Enforces `additionalProperties: false` across all objects.
  - Required top-level keys: `version`, `project`. Optional sections: `worktree`, `agent`, `history`, `doctor`, `prune`, `telemetry`, `concurrency`, `environment`.
  - Supported agent provider tokens: `copilot`.
- **Workflow V1 (`v1/workflow.json`)**:
  - Validates workflow and task YAML definitions.
  - Enforces schema for `steps`, `inputs`, `defaults`, and `assert` blocks.
- **Project identity V1 (`v1/project.json`)**:
  - Validates closed project identity objects with a lowercase URL-safe identifier, optional non-blank display name, and UTC ISO-8601 creation timestamp.
- **Validation Engine**:
  - Evaluated via `SchemaValidator` (`common/schema_validation.py`).
  - Wraps `jsonschema.Draft202012Validator` and returns a non-raising `ValidationResult(ok, errors)`.
