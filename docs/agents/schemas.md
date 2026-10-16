# Schemas and Entities

Cross-cutting contracts for the entities in the Dovo codebase. This doc records what no single source file shows: invariants spanning modules, merge and resolution rules, and which file owns what. It does **not** list fields, enum members, exception classes, entrypoint methods, or commands. Read the source for those; when a bullet here starts restating them, delete it.

Update this doc only when a contract below changes. Adding a field, enum member, exception, DTO, entrypoint method, or command needs no edit here ([documentation.md](documentation.md)).

---

## 1. Where to read the shape

| To see… | Read |
|:---|:---|
| Exceptions | `src/dovo/common/exceptions.py`, `common/lock.py`, `common/filesystem/exceptions.py`, `core/*/exceptions.py`, `engine/exceptions.py` |
| Error codes | `src/dovo/common/error_codes.py` |
| Result/DTO models | `core/*/models.py`, `common/models.py`, `common/filesystem/models.py`, `engine/models.py`, `engine/state_models.py`, `engine/executors/models.py` |
| Config sections and tiers | `core/config/models.py`, `core/config/services/hierarchical_loader.py` |
| Blueprint and step definitions | `core/catalog/definitions/` |
| Tool policy | `common/tool_policy.py` |
| DB tables | `core/db/models.py` |
| Domain entrypoints | `core/<domain>/<domain>.py` in each domain, and `engine/engine.py` |
| Registered CLI commands | `src/dovo/cli/cli.py`; user behavior in [docs/cli/](../cli/) |
| Agent providers | `core/agents/registry.py` (`PROVIDERS`) |
| Doctor checks | `core/diagnostics/services/registry.py` (`get_default_registry`) |
| JSON Schemas | `src/dovo/schemas/v1/` (`config.json`, `project.json`, `blueprint.json`), validated by `common/schema_validation.py` |

---

## 2. Result/Outcome contract

Operations that can fail return a Pydantic result subclassing `BaseResult` ([`common/models.py`](../../src/dovo/common/models.py)) instead of raising. `ok` is derived from `errors` (and `status`), never stored. `error_code` is `None` on success; the validator that requires it when `errors` are populated is short-circuited pending caller migration (#641), so do not rely on it being enforced. Models are `extra="forbid"` and strict by default; the only `extra="ignore"` exceptions are hand-authored YAML models, each with a justifying comment (MODEL-001).

---

## 3. Paths and ownership

- `RepositoryPaths` holds repo-local paths; `WorkspacePaths` extends it with project-scoped locations that need the project identity ([`common/filesystem/models.py`](../../src/dovo/common/filesystem/models.py)).
- `resolve_workspace_paths` (`core/project/services/storage.py`) loads the required `project_id` from `project.json` and raises `WorkspaceNotInitializedError` when it is missing or unusable.
- Every domain entrypoint and service takes `paths: WorkspacePaths` as its only ambient location state; `CliContext.build()` resolves one snapshot per CLI invocation.
- Project identity is strict and extra-forbidding; it is the only source of `project_id`.

---

## 4. Config

Models live in [`core/config/models.py`](../../src/dovo/core/config/models.py); the JSON Schema is [`schemas/v1/config.json`](../../src/dovo/schemas/v1/config.json). Their keys must agree, enforced by [`tests/lint/test_config_schema_parity.py`](../../tests/lint/test_config_schema_parity.py).

Hierarchical resolution merges four tiers, lowest to highest precedence: **Packaged < Global < User < Repo** (`ConfigTier`). Merge rules not visible from the models:

- Default: a higher tier **replaces** a lower tier's value.
- `environment.sensitive_variables` is **unioned** across the Global, User, and Repo tiers.
- `agents.<provider>.tools` is **atomic**: a higher tier replaces the whole policy (`_is_atomic_key_path` in `hierarchical_loader.py`).
- `agents` merges per provider and then per field across tiers: a higher tier overrides only the providers and fields it names.
- `agents.<provider>.env_passthrough` and `agents.<provider>.env_mode` use the default replace rule, so a repo list replaces a global one.
- `sensitive_variables` and `env_passthrough` hold variable **names only**, never values.
- A Global or User tier that is unreadable, malformed, or invalid surfaces as `ConfigLoadStatus.TIER_INVALID` through `resolve_effective_config`. `Config.load()`, every `Config.<section>` accessor, `dovo config show`, and `dovo run`/`dovo resume` all route through it.

---

## 5. Blueprints, steps, and tool policy

- A single `BlueprintDefinition` models every executable blueprint; there is no `kind` discriminator.
- `StepDefinition.tools` is `ToolPolicy | None`: `None` means omitted (inherit), an explicit object **replaces** any inherited policy whole, and a legacy string list is rejected.
- `type: internal` steps require a non-empty `command` that names an `INTERNAL_COMMAND_HANDLERS` key (`engine/executors/internal_dispatch.py`).
- A step's `artifacts:` block publishes after a successful step, on top-level and loop `do:` sub-steps.
- Providers enforce tool policy; see [Tool Policy](../guides/agent-providers.md#tool-policy).

---

## 6. Catalog

- The catalog is **disk-only**: each of the REPO, USER, and GLOBAL tiers keeps its own `index.json` cache, rebuilt wholesale from a directory walk on every lookup. PACKAGED is read directly from bundled resources. There is no SQLite catalog table.
- Resolution precedence: **REPO, then USER, then GLOBAL, then PACKAGED**.
- Bundled `dovo/` templates are protected from mutation, and deletes only apply to REPO-tier items.

---

## 7. Database

- One centralized SQLite database is shared across projects (`resolve_db_path` in `core/db/connection.py`).
- Every record carries `project_id`, and every `BaseRepository` query scopes on it (`core/db/repositories/base.py`).
- Create one `DovoDb` or repository per command invocation and pass it down; never construct one inside a loop or a helper (PERF-001).
- Fetch a subset of records with a filtered repository query on an indexed column, not by filtering a full fetch in Python (PERF-003).
- Schema or migration changes follow [ci-and-tooling.md](ci-and-tooling.md#database-migration-hygiene).

---

## 8. Run and session state

- The `SessionRecord` row (`execution_state_json`, `execution_state_revision`) is the **canonical** execution state. `<session-dir>/session.json` is a database-derived projection, regenerated from the row when missing, corrupt, or different, and a newer file is rejected rather than imported.
- `SessionStateStore` saves with a revision compare-and-swap and does not lock; callers hold the workspace lock.
- `Engine.run` snapshots the blueprint and every transitively-resolved `uses:` step into `<session_dir>/definitions/`, so resume executes from the snapshot with no live catalog read. Resume fails with `EngineResumeError` when state or snapshots are inconsistent.
- A paused leaf (top-level or loop body) keeps its failed attempt, so resume re-enters the failure prompt without re-running the step; a paused `ask` leaf keeps its resolved `pending_ask` request, so resume asks it again without re-resolving options.
- `session.log` is an append-only timeline; write failures are dropped silently, and the log is read back through `core/sessions/services/read_logs.py`.
- `RunCoordinator` applies one durable transition per node through `SessionStateStore`; loop decisions are pure (`LoopPolicy`) and applied by the coordinator.

---

## 9. Agent providers

- Providers are listed once in `PROVIDERS` ([`core/agents/registry.py`](../../src/dovo/core/agents/registry.py)); the factory and config validation both read it.
- `ResolvedAgentSettings` is resolved once per drive in `drive_run`. A non-empty run-row override replaces `default_agent` and selects the `agents.<provider>` block every setting is read from; `--env-passthrough` entries append to that block's list (de-duplicated) and `--env-mode` overrides its value. `AgentEnvOverrides` carries those flags and is never persisted.
- `ResolvedAgentSettings.tools` is always a concrete policy: `AgentSettings.tools` of the selected provider when set, else `default_tool_policy()`. A step's own `tools` wins over it. `BaseAgentProvider` rejects a policy the provider descriptor cannot enforce before invoking it.
- Each attempt gets an `AgentInvocationContext`. `control_path` is host-owned and must never reach prompts, environments, or grants; `AgentRequest.agent_scratch_path` is the only scratch path exposed to prompts and must equal `invocation.scratch_path`.
- `invocation`, `env`, and `metadata_env` are excluded from serialized dumps.
- Environment inputs are names-only for reporting: `env_withheld` carries the names the allowlist withheld, never values.
- A runner outcome with denials and an empty worktree diff becomes a `blocked` response (built by `blocked_response`).

### Agent progress mapping

A provider reports live progress when its `ProviderSpec.progress_mapper` ([`core/agents/base.py`](../../src/dovo/core/agents/base.py)) is set; `None` means it reports none. Each such provider documents its mapping under its own `### <Provider> progress mapping` heading, and [`tests/lint/test_progress_mapping_parity.py`](../../tests/lint/test_progress_mapping_parity.py) fails when a table disagrees with that provider's tool-kind or target-field constants.

### Copilot progress mapping

The Copilot adapter maps each `tool.execution_start` stdout event through `map_copilot_line` ([`core/agents/copilot_progress.py`](../../src/dovo/core/agents/copilot_progress.py)); other event types and unlisted tools such as `skill` are skipped. The table below is the mapping specification.

| Kind | Tool names | Target |
| --- | --- | --- |
| `read` | `view` | `arguments.path` |
| `search` | `rg`, `grep`, `glob` | `arguments.pattern` |
| `edit` | `create`, `edit`, `apply_patch` | `arguments.path`; for `apply_patch`, each `*** Add File:`, `*** Update File:` or `*** Delete File:` path in the `arguments` string |
| `shell` | `bash` | `arguments.command` |

### Claude progress mapping

The Claude Code adapter maps each `tool_use` block of an `assistant` stdout event through `map_claude_line` ([`core/agents/claude_progress.py`](../../src/dovo/core/agents/claude_progress.py)); other events and unlisted tools such as `WebFetch` are skipped. Each `text` block also yields a `note` item carrying the first non-blank line of its text, and each `tool_result` block of a `user` event yields a `result` item carrying the output and the id of the call it answers; neither is a tool call, so the table below, the tool mapping specification, has no row for them.

| Kind | Tool names | Target |
| --- | --- | --- |
| `read` | `Read` | `input.file_path` |
| `search` | `Grep`, `Glob` | `input.pattern` |
| `edit` | `Edit`, `Write`, `NotebookEdit` | `input.file_path` (`input.notebook_path` for `NotebookEdit`) |
| `shell` | `Bash` | `input.command` |

---

## 10. Artifacts

- `publish_artifact` is reached only through the `type: internal` `artifacts.upload` handler or a step's `artifacts:` block; there is no publish CLI command.
- Each publish adds an immutable revision of `(session_id, name)`; nothing rewrites a published revision, and `ArtifactRecord.path` is authoritative for a revision's directory.
- Download verifies checksums to completion before copying any file into `--dest`.
- Pruning is gated by `prune.remove_expired_artifacts` unless `--force` is passed.

---

## 11. Doctor

- Checks implement the `DiagnosticCheck` protocol and register by unique `check_id`; a duplicate raises `CheckRegistrationError`.
- A failing or warning check carries deterministic `Remediation`s resolved from `error_code`/`details` by `resolve_remediations` (`core/diagnostics/services/remediation.py`). Error codes are defined in `common/error_codes.py`.
- `DoctorCheckView`/`DoctorReportView` reshape the core models field for field for presentation only.

---

## 12. JSON Schemas

- `config.json` validates `.dovo/config.json` with `additionalProperties: false` throughout; `project.json` validates the project identity; `blueprint.json` validates blueprint YAML.
- `SchemaValidator` wraps `jsonschema.Draft202012Validator` and returns a non-raising `ValidationResult(ok, errors)`.
- Parity tests: config keys against the models in [`tests/lint/test_config_schema_parity.py`](../../tests/lint/test_config_schema_parity.py); the README command list against `cli.py` in [`tests/lint/test_readme_command_parity.py`](../../tests/lint/test_readme_command_parity.py).
