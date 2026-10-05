# Glossary

Precise definitions for core concepts and terms in the Dovo CLI codebase.

**Relevant sources:** `src/dovo/core/`, `src/dovo/engine/`

- **Step**: The smallest unit of execution: a command, agent prompt, or script, with optional `assert` conditions and `on_failure` policies.
  - *Model:* `StepDefinition` in [`core/catalog/definitions/step.py`](../../src/dovo/core/catalog/definitions/step.py).
  - *Runner:* `StepExecution` in [`engine/executors/step_executor.py`](../../src/dovo/engine/executors/step_executor.py).
- **Loop Step**: A container step that repeats child steps (`do: []`) until a condition or iteration ceiling is reached.
  - *Model:* `LoopStepBlock` in [`core/catalog/definitions/step.py`](../../src/dovo/core/catalog/definitions/step.py).
  - *Execution:* `RunCoordinator` in [`engine/coordinator.py`](../../src/dovo/engine/coordinator.py), driven by `LoopPolicy` in [`engine/loop_policy.py`](../../src/dovo/engine/loop_policy.py).
- **Task**: A blueprint containing linear steps without loop steps.
- **Workflow**: A blueprint permitted to contain loop steps and multi-step orchestration.
- **Blueprint**: The unified document model and handle representing tasks and workflows.
  - *Model/Facade:* `BlueprintDefinition` in [`core/catalog/definitions/blueprint.py`](../../src/dovo/core/catalog/definitions/blueprint.py) and `Blueprint` in [`core/catalog/blueprint.py`](../../src/dovo/core/catalog/blueprint.py).
- **Catalog**: The disk-only, multi-tier (REPO/USER/GLOBAL/PACKAGED) index of named blueprints and steps, each disk-backed tier rooted under its own `catalog/` directory with a derived `index.json` cache, plus packaged seeds.
  - *Facade:* `Catalog` in [`core/catalog/catalog.py`](../../src/dovo/core/catalog/catalog.py).
- **Run**: The act of executing a blueprint, from start to terminal outcome. A run produces a session; it is not the record that outlives it.
  - *Models:* `RunContext` in [`engine/models.py`](../../src/dovo/engine/models.py), `RunOutcome` in [`engine/models.py`](../../src/dovo/engine/models.py).
- **Run Context**: The infrastructure bundle for one run's execution (session id, paths, target directory, session scratch/log/artifact locations, worktree); durable progress lives in the execution state, not the context.
  - *Model:* `RunContext` in [`engine/models.py`](../../src/dovo/engine/models.py).
- **Run Outcome**: The terminal execution result containing status, step results, warnings, and errors.
  - *Model:* `RunOutcome` in [`engine/models.py`](../../src/dovo/engine/models.py).
- **Session**: The unit a run produces and leaves behind: a unique id (`{kind}_{8-hex}`) tying together its DB record, status, log events, diff artifact, and history in `.dovo/sessions/<id>/`. A session exists if and only if its session record exists.
  - *Model:* `SessionRecord` and `SessionStatus` in [`core/db/models.py`](../../src/dovo/core/db/models.py); `Session` / `SessionCollection` in [`core/sessions/sessions.py`](../../src/dovo/core/sessions/sessions.py).
- **Worktree**: An isolated git worktree checkout (`.dovo/worktrees/<id>/`, branch `dovo/<id>`). A worktree created by `dovo run` takes the run's session id as `<id>`; `dovo worktree create` generates `dovo_<8 hex>`.
  - *Facade/Services:* `Worktree` in [`core/worktree/facade.py`](../../src/dovo/core/worktree/facade.py) and [`core/worktree/services/lifecycle.py`](../../src/dovo/core/worktree/services/lifecycle.py).
- **Checkpoint**: A paused leaf step (top-level or a loop body step) plus its last failed attempt in the run's `ExecutionStateTree`, allowing a paused run (`prompt_user`) to resume by re-entering the failure prompt without re-running the step.
  - *Model:* `ExecutionStateTree` in [`engine/state_models.py`](../../src/dovo/engine/state_models.py).
- **Input (`ParameterInput`)**: A declared, typed parameter in a blueprint referenced via `${{ inputs.<name> }}` placeholders.
  - *Model:* `ParameterInput` in [`core/inputs/models.py`](../../src/dovo/core/inputs/models.py).
- **Engine**: The process-level facade (`Engine` in [`engine/engine.py`](../../src/dovo/engine/engine.py)) managing run persistence and delegating to `drive_run` in [`engine/session.py`](../../src/dovo/engine/session.py), which executes the run through `RunCoordinator` in [`engine/coordinator.py`](../../src/dovo/engine/coordinator.py).
- **Adapter**: Provider-specific implementation of `BaseAgentProvider` (`copilot`) in [`core/agents/`](../../src/dovo/core/agents/).
