# Schemas and Entities

Comprehensive reference for the shape of entities across the Worktree CLI codebase: exceptions, DTOs, domain facades, CLI commands, and JSON/YAML schemas.

---

## 1. Exception Hierarchy and Domain Errors

**Relevant sources:**
- `src/worktree/common/exceptions.py`
- `src/worktree/common/filesystem/exceptions.py`
- `src/worktree/common/lock.py`
- `src/worktree/core/*/exceptions.py`

### Base Definition Exceptions
- `DefinitionError` (`common/exceptions.py`): Base exception for catalog-backed domain definitions.
- `DefinitionNotFoundError` (`common/exceptions.py`): Definition file or identifier not found.
- `DefinitionLoadError` (`common/exceptions.py`): YAML syntax or parse failure when reading definition.
- `DefinitionValidationError` (`common/exceptions.py`): Schema or Pydantic validation failure.

### Domain Exceptions
- **Blueprint** (`core/blueprint/exceptions.py`):
  - `BlueprintNotFoundError` (subclasses `DefinitionNotFoundError`)
  - `BlueprintLoadError` (subclasses `DefinitionLoadError`)
  - `BlueprintValidationError` (subclasses `DefinitionValidationError`)
- **Step** (`core/step/exceptions.py`):
  - `StepNotFoundError` (subclasses `DefinitionNotFoundError`)
  - `StepValidationError` (subclasses `DefinitionValidationError`)
- **Catalog** (`core/catalog/exceptions.py`):
  - `CatalogError` (subclasses `DefinitionError`): Base catalog exception.
  - `CatalogFileNotFoundError`: Specified catalog resource not on disk.
  - `CatalogYamlError`: YAML syntax error when parsing catalog item.
  - `CatalogWriteError`: File write or permission failure during catalog mutation.
  - `CatalogProtectionError`: Attempted delete/mutate of a protected bundled `wt/` template.
  - `CatalogTierDeleteError`: Attempted delete of a catalog item resolved from a non-REPO tier.
- **Config** (`core/config/exceptions.py`):
  - `ConfigLoadError`: Fatal configuration loading failure.
  - `ConfigTierValidationError`: A hierarchical config tier file is unreadable, malformed, or fails `WorktreeConfig` validation; carries `tier`, `path`, and `details`.
- **Engine** (`core/engine/exceptions.py`):
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
- **Sandbox** (`core/sandbox/exceptions.py`):
  - `SandboxError`: Base sandbox failure.
  - `SandboxConfigError`: Invalid sandbox configuration parameters.
  - `SandboxCapacityError`: Active sandbox limit reached.
- **Patch** (`core/patch/exceptions.py`):
  - `MalformedDiffHeader`: Invalid unified diff format.
- **Doctor** (`core/doctor/exceptions.py`):
  - `DoctorError`: Base exception for doctor domain errors.
  - `CheckRegistrationError`: Raised when registering a check with an existing check_id.
- **Lock** (`common/lock.py`):
  - `LockTimeoutError`: Timeout acquiring `.worktree/.lock` advisory lock.
- **Global filesystem** ([`common/filesystem/exceptions.py`](../../src/worktree/common/filesystem/exceptions.py)):
  - [`InvalidGlobalRootError`](../../src/worktree/common/filesystem/exceptions.py): The selected global Worktree root contains a `.git` directory.

---

## 2. DTOs, Results, and Outcome Models

**Relevant sources:**
- `src/worktree/core/*/models.py`
- `src/worktree/common/models.py`
- `src/worktree/common/filesystem/models.py`

### Result/Outcome Pattern
All operations that can fail return a Pydantic result object subclassing `BaseResult` instead of raising:
- `status: StrEnum`: Machine-readable outcome state.
- `errors: list[str]`: Fatal error messages (inherited from `BaseResult`).
- `warnings: list[str]`: Non-fatal warning messages (inherited from `BaseResult`).
- `fixes: list[str]`: Suggested fixes or remediations (inherited from `BaseResult`).
- `error_code: str | None`: Machine-readable failure code from `worktree.common.error_codes.ErrorCode` or a plain string (inherited from `BaseResult`); `None` on success. A model validator enforces presence when `errors` or `remediations` are populated, currently short-circuited pending caller migration (issue #641).
- `ok: bool`: Property returning `True` when `not bool(self.errors)` or `status == OK`.
- Standard configuration: `model_config = {"extra": "forbid", "strict": True}` on `BaseResult`.

### Configuration Models
**Relevant sources:** `src/worktree/core/config/models.py`, `loader.py`, `validate.py`, `mutate.py`, `generator.py`.
- `WorktreeConfig`: Root configuration object, including `ignore_global_root_error`, defined in [`core/config/models.py`](../../src/worktree/core/config/models.py).
- Section configs: `ProjectConfig`, `SandboxConfig`, `AgentConfig`, `HistoryConfig`, `DoctorConfig`, `PruneConfig`, `TelemetryConfig`, `ConcurrencyConfig`.
- `ConfigLoadResult`: Result of loading and validating `.worktree/config.json` (`status`, `config_path`, `raw`, `config`, `errors`, `ok`). `ConfigLoadStatus.TIER_INVALID` classifies a Global or User tier failure surfaced by `resolve_effective_config` ([`core/config/services/resolve.py`](../../src/worktree/core/config/services/resolve.py)), which `Config.load()`/`Config._loaded_config` — and therefore every `Config.<section>` accessor, `wt config show`, and blueprint execution (`wt run`/`wt resume`) — route through.
- `ConfigValidationResult`: Result of semantic config validation (`status`, `config_path`, `raw`, `config`, `errors`, `warnings`, `ok`).
- `ConfigSetResult`: Result of mutating a dot-path key in config (`status`, `config_path`, `key`, `value`, `errors`, `ok`).
- `ConfigUnsetResult`: Result of removing a dot-path key from config (`status`, `config_path`, `key`, `existed`, `previous_value`, `errors`, `ok`).
- `ConfigGenerationResult`: Result of creating, repairing, or overwriting config (`created`, `skipped_existing`, `repaired`, `overwritten`, `inserted_keys`, `warnings`, `errors`, `ok`).
- [`GlobalPaths`](../../src/worktree/common/filesystem/models.py): Canonical paths for the global Worktree hierarchy.
- [`RepositoryPaths`](../../src/worktree/common/filesystem/models.py): Repo-local paths resolved from a root directory (`root_dir`, `worktree_dir`, `config_file`, `catalog_dir`, `catalog_steps_dir`, `catalog_blueprints_dir`, `sandboxes_dir`, `lock_file`, `gitignore_file`). Built via `RepositoryPaths.from_root(root)`.
- [`WorkspacePaths`](../../src/worktree/common/filesystem/models.py): Extends `RepositoryPaths` with project-scoped, identity-dependent locations (`catalog_templates_dir`, `global_paths`, `database_file`, `project_id`, `runtime_root`, `logs_dir`, `sessions_dir`, `artifacts_dir`, `tmp_dir`) plus `session_dir(id)`/`sandbox_dir(id)`/`catalog_dir_for(tier)` helpers. Built via `resolve_workspace_paths(repository_paths, global_paths)` ([`core/project/services/storage.py`](../../src/worktree/core/project/services/storage.py)), which resolves `project_id` from `project.json` when present; projects without an identity retain repository-local runtime paths. Every domain facade/service takes `paths: WorkspacePaths` as its single source of ambient location state — see [architecture.md](architecture.md#path-ownership-repositorypaths--workspacepaths).
- `ConfigTier`, `ConfigLayer`: Precedence-tier enum and resolved-layer DTO for hierarchical config resolution, defined in [`core/config/models.py`](../../src/worktree/core/config/models.py). [`core/config/services/hierarchical_loader.py`](../../src/worktree/core/config/services/hierarchical_loader.py) exposes `load_hierarchical_config` and `resolve_config_layers`, merging Packaged, Global, User, and Repo tiers into a validated `WorktreeConfig`.
- `HierarchicalConfigLoadResult` / `HierarchicalConfigLoadStatus`: Non-raising result of `load_hierarchical_config` (`status`, `tier`, `path`, `config`, `errors`, `ok`); `status` classifies which tier's file was unreadable, malformed, non-object, or failed `WorktreeConfig` validation.

### Project Identity Model
**Relevant source:** `src/worktree/core/project/models.py`.
- `ProjectIdentity`: Strict, extra-forbidding stable project identity. Its identifier is lowercase URL-safe text from 3 to 63 characters, optional display names must contain a non-whitespace character, and creation timestamps must be UTC. Its classified persistence DTOs and non-raising load/save behavior are defined alongside it; the operations are in `src/worktree/core/project/services/identity.py`.
- `ProjectIdentityProvisionResult` / `ProjectIdentityProvisionStatus`: Non-raising outcome of `wt init`'s create-or-preserve-or-overwrite identity provisioning (`src/worktree/core/project/services/identity.py:provision_project_identity`).

### Blueprint & Step Models
**Relevant sources:** `src/worktree/core/blueprint/models.py`, `src/worktree/core/step/models.py`, `src/worktree/core/inputs/models.py`.
- `BlueprintDefinition`: Unified model for executable blueprints. See [`src/worktree/core/blueprint/models.py`](../../src/worktree/core/blueprint/models.py) for its fields; it carries no `kind` discriminator.
- `BlueprintDefaults`: Blueprint-level defaults (`on_failure`).
- `ParameterInput`: Declared parameter input (`type`, `description`, `required`, `default`, `aliases`).
- `InputResolveResult`: Result of resolving input values from CLI flags and defaults (`values`, `missing`, `errors`, `warnings`, `ok`).
- `StepDefinition`: Executable step specification (`id`, `name`, `type`, `description`, `command`, `prompt`, `script_path`, `tools`, `env`, `timeout_seconds`, `assert_`, `on_failure`, `uses`, `run`, `artifacts`). `type` is a `StepType` (`command`, `agent`, `script`, `internal`); `internal` requires a non-empty `command` naming an `INTERNAL_COMMAND_HANDLERS` registry key (`core/step/services/internal_dispatch.py`). `artifacts: list[ArtifactPublishSpec]` declares bundles auto-published via `publish_artifact` after a successful step, on both top-level and loop `do:` sub-steps; see Artifacts Models below.
- `ArtifactPublishSpec`: Declarative per-step artifact publish spec (`name`, `path`, `retention_days`).
- `InternalCommandContext`: Structured inputs passed to an in-process `type: internal` handler (`sandbox_path`, `session_id`, `env`, `artifacts_dir`, `artifacts_db`); see [`src/worktree/core/step/models.py`](../../src/worktree/core/step/models.py).
- `ExecutionMetadata.session_id`: The run's session ID, threaded from `RunContext.session_id` through `build_execution_metadata` and exposed to `type: command`/`script` steps as the `WT_SESSION_ID` environment variable (`core/step/services/metadata.py`).
- `StepAssert`: Verification conditions (`exit_code`, `output_contains`, `output_not_contains`, `regex_match`, `json_match`, `file_exists`, `file_not_exists`, `file_not_empty`).
- `FailurePolicy`: `StrEnum` (`abort`, `continue`, `prompt_user`, `retry`). Terminal policies exclude `retry`.
- `FailureSpec`: Normalized failure policy (`action`, `max_retries`, `backoff_ms`, `on_max_retries`).
- `LoopStepBlock`: Step container repeating `do: []` until `until` condition or `max_iterations` (`id`, `type="loop"`, `max_iterations`, `until`, `do`, `on_max_iterations`).
- `StepResult`: Step execution outcome (`step_id`, `status`, `exit_code`, `stdout`, `stderr`, `duration_seconds`, `attempts`, `error_message`, `outputs`, `ok`).
- `AssertionResult`: Assertion evaluation outcome (`passed`, `failed_conditions`, `message`).

### Run Engine Models
**Relevant sources:** `src/worktree/core/engine/models.py`, `src/worktree/core/engine/context.py`, `src/worktree/core/logs/models.py`.
- [`RunContext`](../../src/worktree/core/engine/models.py): Immutable input bundle for sandbox/session setup and step coordination (`use_sandbox`, `keep`, `agent`, `observer`, `inputs`, `no_tty`, `failure_prompter`, `auto_apply`, `sandbox_id`, `paths`).
- [`RunSessionContext`](../../src/worktree/core/engine/context.py): Infrastructure resources for one run's execution; durable progress lives only in `ExecutionStateTree`.
- [`RunOutcome`](../../src/worktree/core/engine/models.py): Terminal run result, including the sandbox and session identifiers.
- [`StepLoopState`](../../src/worktree/core/engine/models.py): Mutable per-run bookkeeping threaded through step execution.
- [`RunObserver`](../../src/worktree/core/engine/models.py), [`FailurePrompter`](../../src/worktree/core/engine/models.py), [`FailurePromptDecision`](../../src/worktree/core/engine/models.py), [`LoopPromptDecision`](../../src/worktree/core/engine/models.py): Caller-supplied progress hooks and failure/loop decision entrypoints.
- `RunStatus`: `StrEnum` (`pending`, `running`, `completed`, `failed`, `paused`, `cancelled`).
- `RunRequest`: Facade execution parameters for `Engine.run` (`inputs`, `cli_args`, `use_sandbox`, `keep`, `agent`, `session_id`, `observer`, `failure_prompter`, `no_tty`).
- [`RunCoordinator`](../../src/worktree/core/engine/coordinator.py) / `NodeTransitionKind`: State-driven execution of a run: selects the next non-terminal node, applies one durable transition through `RunStateStore`, and repeats until the run completes, pauses, or fails. `Engine.run` and `Engine.resume` reach it through `drive_run` ([`session.py`](../../src/worktree/core/engine/session.py)).
- [`EngineLoader`](../../src/worktree/core/engine/loader.py): Validates a paused run's row, execution state, snapshots, and retained sandbox; raises `EngineResumeError` carrying an `EngineResumeStatus`.
- [`flatten_step_results`](../../src/worktree/core/engine/projection.py): Projects the terminal leaf attempts of an `ExecutionStateTree` into the ordered `RunOutcome.step_results`.
- `EngineResumeStatus`: `StrEnum` (`ok`, `not_found`, `wrong_status`, `missing_sandbox`, `corrupt_state`, `missing_snapshot`, `failed`).
- [`ExecutionStateTree`](../../src/worktree/core/engine/state_models.py): Versioned plan-and-progress document for one run (manifest plus ordered step and loop nodes). A paused leaf (top-level or loop body) keeps its failed attempt, so resume re-enters the failure prompt without re-running the step. `ExecutionLoopNode.granted_iterations` records ceiling grants; the effective ceiling is `max_iterations + granted_iterations`.
- [`RunStateStore`](../../src/worktree/core/engine/state_store.py): Builds, saves (revision compare-and-swap), and loads an `ExecutionStateTree` for one run row; returns `RunStateWriteResult` / `RunStateLoadResult` (`RunStateWriteStatus` / `RunStateLoadStatus` in [`state_models.py`](../../src/worktree/core/engine/state_models.py)). Does not lock; callers hold the workspace lock.
- [`RunStartConfig`](../../src/worktree/core/engine/models.py): Resolved run options written to the run row when a run starts.
- `DefinitionRef`: One snapshotted catalog item's resolved reference, content SHA, and resolution timestamp (`ref`, `sha`, `resolved_at`); `ref` is `"<tier>:<item_type>:<key>"`.
- `DefinitionsManifest`: The blueprint's `DefinitionRef` plus a `DefinitionRef` per transitively-resolved `uses:` step (`blueprint`, `steps`), snapshotted by `Engine.run` into `<session_dir>/definitions/` and consumed by `RunCoordinator`/`load_blueprint_from_snapshot` to execute and resume without a live catalog read.
- [`RunLogEvent`](../../src/worktree/core/logs/models.py) / `RunLogEventType`: One `run.log` timeline record. `drive_run`, `RunCoordinator`, `StepCoordinator`, and `LoopEventEmitter` append one JSON line per lifecycle event to `logs_dir/<session_id>/run.log` via `append_run_log_event` ([`core/logs/services/write.py`](../../src/worktree/core/logs/services/write.py)), which stamps `ts` at write time and drops write failures silently. `event` decides which optional fields are populated. `core/logs` reads these events back.
- [`StepCoordinator`](../../src/worktree/core/engine/step_executor.py), [`Workspace`](../../src/worktree/core/engine/workspace.py): Per-step attempt and failure-prompt primitives and sandbox/session lifecycle used by the coordinator.
- [`LoopPolicy`](../../src/worktree/core/engine/loop_policy.py) / `LoopDecision` / `LoopTransitionKind`: Pure loop decisions (advance a body step, complete an iteration, repeat, terminate, or apply the `max_iterations` ceiling) that `RunCoordinator.advance_loop` applies durably. [`LoopEventEmitter`](../../src/worktree/core/engine/loop_events.py) emits the loop `run.log` events and observer callbacks; `validate_loop_structure` in [`state_validation.py`](../../src/worktree/core/engine/state_validation.py) rejects persisted loop state whose ids or body order differ from the run snapshot.

### Agent Provider Models
**Relevant sources:** `src/worktree/core/agents/models.py`, `src/worktree/core/agents/cli_mutation.py`.
- `AgentRequest`: Input payload to agent adapter (`prompt`, `failure_payload`, `timeout_seconds`, `config`, `sandbox_path`).
- `AgentResponse`: Adapter outcome (`status`, `patch`, `errors`, `warnings`, `summary`, `ok`).
- `AgentResponseStatus`: `StrEnum` (`proposed_patch`, `no_op`, `unfixable`, `timeout`, `provider_error`).
- `AgentFailurePayload`: Step failure context captured for agent prompts (`step_id`, `command`, `exit_code`, `stdout`, `stderr`, `duration_seconds`, `error_message`, `files`).
- `CliMutationRunRequest`: Subprocess execution payload for direct-mutation adapters (`prompt`, `sandbox_path`, `timeout_seconds`, `env`).
- `CliMutationOutcome`: Direct-mutation subprocess result (`success`, `exit_code`, `stdout`, `stderr`, `error_message`).

### Sandbox Models
**Relevant sources:** `src/worktree/core/sandbox/models.py`.
- `SandboxSession`: Active sandbox metadata (`session_id`, `sandbox_path`, `target_branch`, `base_commit`, `created_at`, `status`).
- `SandboxCreateResult`: Result of creating sandbox worktree (`status`, `session_id`, `sandbox_path`, `branch_name`, `errors`, `warnings`, `ok`).
- `SandboxDeleteResult`: Result of deleting sandbox (`status`, `session_id`, `sandbox_path`, `branch_deleted`, `errors`, `warnings`, `ok`).
- `SandboxPruneResult`: Result of pruning stale/orphan sandboxes (`status`, `pruned_items`, `errors`, `warnings`, `ok`).
- `SandboxListResult`, `SandboxShowResult`, `SandboxApplyResult`, `SandboxDiffResult`, `SandboxDetectionResult`.

### Artifacts Models
**Relevant sources:** `src/worktree/core/artifacts/models.py`.
- `ArtifactManifest` / `ArtifactManifestFile`: `manifest.json` contents written alongside every published artifact bundle (`name`, `session_id`, `created_at`, `expires_at`, `size_bytes`, `file_count`, `files: list[ArtifactManifestFile]`; each file entry carries `path`, `sha256`, `size_bytes`).
- `ArtifactUploadResult` / `ArtifactUploadStatus`: Result of `publish_artifact` (`ok`, `no_matching_files`, `error`); constructed only by `core/artifacts/services/upload.py` and the `type: internal` `artifacts.upload` handler — never dispatched through `ui_dispatcher`.
- `ArtifactDownloadResult` / `ArtifactDownloadStatus`: Result of `download_artifact` (`ok`, `not_found`, `checksum_mismatch`, `error`); checksum verification runs to completion before any file is copied into `--dest`.
- `ArtifactsListResult` / `ArtifactsListStatus`: Result of listing artifacts for the current project (`ok`).
- `ArtifactsPruneResult` / `ArtifactsPruneStatus` / `PrunedArtifact`: Result of `wt artifacts prune` (`ok`, `disabled`, `locked`); `disabled` means `prune.remove_expired_artifacts` is `False` and `--force` was not passed.

### Catalog Models
**Relevant sources:** `src/worktree/core/catalog/models.py`.

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
**Relevant sources:** `src/worktree/core/db/models.py`.

All four tables live in one centralized SQLite database shared across projects (`resolve_db_path` in `core/db/connection.py`), and every record carries `project_id`; every `BaseRepository` query scopes on it (`core/db/repositories/base.py`). The catalog is no longer one of them — see Catalog Models above.
- `SandboxRecord`: Persisted sandbox rows in `sandboxes` table.
- `RunRecord`: Persisted blueprint run rows in `runs` table (including the execution-state/config columns — see [`RunRecord`](../../src/worktree/core/db/models.py); `sandbox_id` is written by `RunCoordinator` when it creates or reuses a sandbox session). The row (`execution_state_json`, `execution_state_revision`) is the canonical execution state; `<session-dir>/run.json` is its projection, regenerated from the row on load when missing, corrupt, or older, and a newer file is rejected rather than imported.
- `CostRecord`: Persisted token and execution cost tracking in `costs` table.
- `ArtifactRecord`: Persisted artifact metadata rows in `artifacts` table (`id`, `project_id`, `session_id`, `name`, `path`, `size_bytes`, `file_count`, `created_at`, `expires_at`); unique on `(project_id, session_id, name)`, upserted by `ArtifactsRepository.create`.

### History, Diff, and Status Models
**Relevant sources:** `src/worktree/core/history/models.py`, `src/worktree/core/diff/models.py`, `src/worktree/core/status/models.py`.
- `HistoryListResult`, `HistoryShowResult`: History query results. `HistoryShowResult.log_files`/`log_snippet` are populated only by `History.show(include_logs=True)`; the snippet holds the last 10 `run.log` events as plain text lines.
- `LogsShowResult` / `LogsShowStatus` / `LogStreamFilter` ([`core/logs/models.py`](../../src/worktree/core/logs/models.py)): `wt logs` outcome. Only one of `events` (parsed `run.log`, no `step` filter) or `lines` (raw step capture, `step` filter) is populated per call. `available_steps`/`available_attempts` are filled on `STEP_NOT_FOUND`/`ATTEMPT_NOT_FOUND`. Step identity is parsed from the capture filename `<NN>_<step_id>[_iter_<n>]_attempt_<n>.<stream>.log`; there is no sidecar index.
- `DiffResult`: Session unified-diff and artifact outcome (`status`, `diff_text`, `files_changed`, `errors`, `ok`).
- `WorktreeStatusResult`: Workspace health, repository status, and collected developer warnings.

### Doctor Models
**Relevant sources:** `src/worktree/core/doctor/models.py`, `src/worktree/core/doctor/services/runner.py`, `src/worktree/core/doctor/services/remediation.py`.
- `CheckStatus`: `StrEnum` (`ok`, `warning`, `failed`, `skipped`).
- `CheckCategory`: `StrEnum` (`git`, `config`, `filesystem`, `sandbox`, `agent`, `environment`).
- `DoctorContext`: Contextual environment supplied to checks (`cwd`, `config`).
- `RemediationType`: `StrEnum` classifying how a remediation is carried out.
- `Remediation`: Deterministic, copy-pasteable remediation action for a failing or warning check.
- `DiagnosticCheckResult`: Individual check outcome model defined in `src/worktree/core/doctor/models.py`, including its `remediations: list[Remediation]` field; `error_code` is inherited from `BaseResult`, not defined on this class.
- `DoctorReport`: Aggregated execution report model defined in `src/worktree/core/doctor/models.py`.
- `DiagnosticCheck`: Protocol defining check identification and execution contract.
- Built-in checks (`src/worktree/core/doctor/checks/`, registered by `get_default_registry()` in `src/worktree/core/doctor/services/registry.py`):
  - `git.repo` (`GitRepoCheck`): validates the `git` binary is on `PATH` and `context.cwd` is a Git repository.
  - `config.schema` (`ConfigSchemaCheck`): validates `.worktree/config.json` exists and passes schema V1 validation.
  - `filesystem.writable` (`FilesystemWritableCheck`): probes write access across `WorkspacePaths`-declared directories.
  - `sandbox.refs` (`SandboxRefsCheck`): validates registered sandbox directories against `SandboxesRepository` and Git worktree state.
  - `env.binaries` (`EnvBinariesCheck`): validates required host and active agent provider CLI binaries are on `PATH`.
  - `agent.setup` (`AgentSetupCheck`): validates the active agent provider's credential and configured model.
- Error codes:
  - `DOCTOR_CHECK_CRASH`: Diagnostic check threw an unhandled exception during execution.
  - `DOCTOR_GIT_BINARY_MISSING`: `git` executable not found on `PATH`.
  - `DOCTOR_GIT_NOT_REPO`: `context.cwd` is not a valid Git repository or worktree.
  - `DOCTOR_CONFIG_NOT_FOUND`: `.worktree/config.json` does not exist.
  - `DOCTOR_CONFIG_MALFORMED`: `.worktree/config.json` contains invalid JSON syntax.
  - `DOCTOR_CONFIG_SCHEMA_INVALID`: `.worktree/config.json` fails schema V1 validation, has a non-object root, is a directory, or is unreadable.
  - `DOCTOR_FS_UNWRITABLE`: One or more configured workspace paths rejected a probe write.
  - `DOCTOR_SANDBOX_STALE`: Stale or broken sandbox references detected.
  - `DOCTOR_SANDBOX_ORPHAN`: Unregistered sandbox worktree folders found.
  - `DOCTOR_BINARY_MISSING`: Configured provider binary is missing from `PATH`.
  - `DOCTOR_AGENT_KEY_MISSING`: Required API key environment variable is missing.
  - `DOCTOR_AGENT_NO_MODEL`: Agent provider model is not configured.
- `resolve_remediations(result) -> list[Remediation]` / `format_remediation_summary(remediations) -> str` (`core/doctor/services/remediation.py`): deterministic mapping from `error_code`/`details` to remediation actions, and a terminal/log-friendly text renderer.
- `DoctorCheckView`, `DoctorReportView` (`src/worktree/cli/ui/formatters/doctor/doctor_views.py`): CLI presentation reshaping of `DiagnosticCheckResult`/`DoctorReport` for `wt doctor`, field for field.

---

## 3. Domain Facades

**Relevant sources:**
- `src/worktree/core/*/facade.py`
- `src/worktree/common/filesystem/facade.py`
- `src/worktree/core/engine/engine.py`
- `src/worktree/core/git/runner.py`

Each core domain exposes a cohesive facade class that encapsulates domain services, queries, and repositories:

| Facade | Module | Key Responsibilities & Methods |
|:---|:---|:---|
| `Bootstrap` | `core/bootstrap/facade.py` | Idempotent workspace initialization and repair (`ensure_workspace`, `initialize_workspace`). |
| `GitRunner` | `core/git/runner.py` | Low-level git CLI execution (`run`, `worktree_add`, `worktree_remove`, `worktree_list`, `diff`). |
| `Sandbox` | `core/sandbox/facade.py` | Worktree sandbox lifecycle (`create`, `show`, `list`, `delete`, `prune`, `apply`, `diff`). |
| `Config` | `core/config/facade.py` | Config loading, validation, generation, and mutation (`load`, `validate`, `set`, `unset`, `generate`, `show`). |
| `WorktreeDb` | `core/db/db.py` | Central database access point (`sandboxes`, `runs`, `costs`, `artifacts` repositories). |
| `Artifacts` | `core/artifacts/artifacts.py` | Session artifact publishing, listing, downloading, and pruning (`upload`, `list`, `download`, `prune`). |
| `Inputs` | `core/inputs/facade.py` | Input flag parsing, default resolution, and placeholder interpolation (`parse_args`, `resolve`, `interpolate`). |
| `Catalog` | `core/catalog/catalog.py` | Disk-only, multi-tier template scanning, indexing, retrieval, and seeding (`list`, `show`, `get`, `create`, `delete`, `sync`, `validate`, `seed`). |
| `Blueprint` | `core/blueprint/facade.py` | Loading and rendering unified blueprint documents (`load`, `from_path`, `from_document`, `render_show`). |
| `Diff` | `core/diff/facade.py` | Session diff calculation, artifact loading, and rendering (`get_diff`, `render`). |
| `Status` | `core/status/facade.py` | Workspace health and telemetry aggregation (`collect`). |
| `History` | `core/history/history.py` | Execution history query and display (`list`, `show`). |
| `Logs` | `core/logs/logs.py` | Persisted session log inspection (`show`); session existence is checked against `RunsRepository`, then the global `logs_dir/<session_id>/`. |
| `Step` | `core/step/facade.py` | Step blueprint load, resolution, and isolated execution (`load`, `resolve`, `execute`, `assert_step`). |
| `Engine` | `core/engine/engine.py` | Process-level run persistence, session minting, execution, and resume (`run`, `resume`, `reconcile`). |
| `Filesystem` | `common/filesystem/facade.py` | Atomic writes, safe path operations, and YAML parsing (`atomic_write_json`, `atomic_write_text`, `read_yaml`). |

---

## 4. Commands and CLI Surface

**Relevant sources:**
- `src/worktree/cli/cli.py`
- `src/worktree/cli/<name>/`

### Entrypoint and Global Options
- Application: `app = typer.Typer(cls=WorktreeTyperGroup, name="wt")` in `src/worktree/cli/cli.py`.
- Global options:
  - `-p, --path`: Workspace root directory.
  - `-v, --verbose`: Verbose telemetry logging.
  - `--version`: Print CLI version and exit.

### Subcommand Structure
Each CLI command package under `src/worktree/cli/<name>/` contains:
- `app.py`: Subcommand Typer app registration.
- `commands/`: Individual command implementations (e.g. `root.py`, `commands/<action>.py`) returning core domain `*Result` models.
- `formatters.py`: UI dispatcher component formatters for core domain `*Result` models.
- `renderers.py`: Rich-based terminal presentation functions.

### Registered CLI Commands
- `wt init`: Initialize workspace, generate `.worktree/` directory, `project.json` identity, `.gitignore`, and `config.json` (`--id`, `--display-name`, `--force`).
- `wt status`: Show workspace health, active sandboxes, and developer warnings.
- `wt config`: Manage configuration (`show`, `set`, `validate`).
- `wt blueprint`: Manage catalog blueprint items across all tiers (`list`/`ls`, `show`, `create`, `delete`, `validate`).
- `wt step`: Manage catalog step items across all tiers (`list`/`ls`, `show`, `create`, `delete`, `validate`).
- `wt run`: Execute a task or workflow blueprint.
- `wt resume`: Resume a paused execution session.
- `wt sandbox`: Manage git worktree sandboxes (`create`, `list`, `show`, `delete`, `prune`, `apply`).
- `wt history`: Query past run records (`history`, `history show`, `history show --logs`).
- `wt logs <session_id>`: Show a session's `run.log` timeline, or one step's raw capture (`--step`, `--attempt`, `--stream`, `--tail`, `--format`).
- `wt artifacts`: Publish, list, download, and prune session artifacts (`list`, `download`, `prune`; publishing is reached only through the `type: internal` `wt/upload-artifact` catalog step or a step's declarative `artifacts:` block, not a CLI command).
- `wt diff`: Show uncommitted or session diffs.
- `wt doctor`: Run registered diagnostic checks and print a scannable workspace health report (`--category`, `--format`).

---

## 5. JSON and YAML Schemas

**Relevant sources:**
- `src/worktree/schemas/v1/config.json`
- `src/worktree/schemas/v1/project.json`
- `src/worktree/schemas/v1/workflow.json`
- `src/worktree/common/schema_validation.py`

### Schema Contracts
- **Config V1 (`v1/config.json`)**:
  - Validates `.worktree/config.json`.
  - Enforces `additionalProperties: false` across all objects.
  - Required top-level keys: `version`, `project`, `sandbox`, `agent`, `history`, `doctor`, `prune`, `telemetry`, `concurrency`.
  - Supported agent provider tokens: `local`, `ollama`, `cursor`, `gemini`, `copilot`, `openai`, `anthropic`, `azure_openai`, `custom`.
- **Workflow V1 (`v1/workflow.json`)**:
  - Validates workflow and task YAML definitions.
  - Enforces schema for `steps`, `inputs`, `defaults`, and `assert` blocks.
- **Project identity V1 (`v1/project.json`)**:
  - Validates closed project identity objects with a lowercase URL-safe identifier, optional non-blank display name, and UTC ISO-8601 creation timestamp.
- **Validation Engine**:
  - Evaluated via `SchemaValidator` (`common/schema_validation.py`).
  - Wraps `jsonschema.Draft202012Validator` and returns a non-raising `ValidationResult(ok, errors)`.
