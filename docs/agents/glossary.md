# Glossary

Precise definitions for core concepts and terms in the Worktree CLI codebase.

**Relevant sources:** `src/worktree/core/`

- **Step**: The smallest unit of execution: a command, agent prompt, or script, with optional `assert` conditions and `on_failure` policies.
  - *Model:* `StepDefinition` in [`core/step/models.py`](../../src/worktree/core/step/models.py).
  - *Runner:* `StepExecution` in [`core/step/runner.py`](../../src/worktree/core/step/runner.py).
- **Loop Step**: A container step that repeats child steps (`do: []`) until a condition or iteration ceiling is reached.
  - *Model:* `LoopStepBlock` in [`core/step/models.py`](../../src/worktree/core/step/models.py).
  - *Runner:* `LoopBlockRunner` in [`core/runtime/loop_runner.py`](../../src/worktree/core/runtime/loop_runner.py).
- **Task**: A blueprint containing linear steps without loop steps.
- **Workflow**: A blueprint permitted to contain loop steps and multi-step orchestration.
- **Blueprint**: The unified document model and handle representing tasks and workflows.
  - *Model/Facade:* `BlueprintDefinition` and `Blueprint` in [`core/blueprint/`](../../src/worktree/core/blueprint/).
- **Catalog**: The disk-only, multi-tier (REPO/USER/GLOBAL/PACKAGED) index of named blueprints and steps, each disk-backed tier rooted under its own `catalog/` directory with a derived `index.json` cache, plus packaged seeds.
  - *Facade:* `Catalog` in [`core/catalog/catalog.py`](../../src/worktree/core/catalog/catalog.py).
- **Run**: A single execution of a blueprint from start to terminal outcome.
  - *Models:* `RunContext`, `RunOutcome` in [`core/runtime/models.py`](../../src/worktree/core/runtime/models.py).
- **Run Context**: The immutable input bundle for a run: steps, cwd, options, inputs, and resume checkpoints.
  - *Model:* `RunContext` in [`core/runtime/models.py`](../../src/worktree/core/runtime/models.py).
- **Run Outcome**: The terminal execution result containing status, step results, warnings, and errors.
  - *Model:* `RunOutcome` in [`core/runtime/models.py`](../../src/worktree/core/runtime/models.py).
- **Session**: Unique execution identifier (`{kind}_{8-hex}`) linking a run to its DB record in the centralized database and session artifacts in `.worktree/sessions/<id>/`.
- **Sandbox**: An isolated git worktree checkout (`.worktree/sandboxes/<session_id>/`, branch `worktree/sandbox-<id>`).
  - *Facade/Services:* `Sandbox` in [`core/sandbox/facade.py`](../../src/worktree/core/sandbox/facade.py) and [`core/sandbox/services/lifecycle.py`](../../src/worktree/core/sandbox/services/lifecycle.py).
- **Checkpoint**: Serialized state (`RunCheckpoint`) allowing paused runs (`prompt_user`) to resume later from the failed step.
  - *Model:* `RunCheckpoint` in [`core/runtime/models.py`](../../src/worktree/core/runtime/models.py).
- **Input (`ParameterInput`)**: A declared, typed parameter in a blueprint referenced via `${{ inputs.<name> }}` placeholders.
  - *Model:* `ParameterInput` in [`core/inputs/models.py`](../../src/worktree/core/inputs/models.py).
- **Engine**: The process-level facade (`Engine` in [`core/engine/engine.py`](../../src/worktree/core/engine/engine.py)) managing run persistence and delegating to `run_steps` in [`core/runtime/run.py`](../../src/worktree/core/runtime/run.py).
- **Adapter**: Provider-specific implementation of `AgentAdapter` (`local`, `ollama`, `cursor`, `gemini`, `copilot`) in [`core/agents/`](../../src/worktree/core/agents/).
