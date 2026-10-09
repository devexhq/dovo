"""Class-based execution service for blueprint run commands."""

from __future__ import annotations

from dataclasses import dataclass, field

from dovo.common.filesystem import WorkspacePaths
from dovo.core.agents.models import AgentEnvMode
from dovo.core.catalog import Catalog
from dovo.core.catalog.blueprint import Blueprint
from dovo.core.catalog.exceptions import BlueprintLoadError, BlueprintNotFoundError, BlueprintValidationError
from dovo.core.db import SessionsRepository
from dovo.core.inputs.services.resolve import format_input_error_message
from dovo.core.sessions import reconcile_stale_sessions
from dovo.engine.engine import Engine
from dovo.engine.exceptions import EngineInputError, EngineRuntimeError
from dovo.engine.models import BlueprintRunResult, FailurePrompter, RunObserver, RunRequest
from dovo.engine.services._shared import fail, finalize, resolve_env_overrides


@dataclass
class BlueprintRunService:
    """Service encapsulating the blueprint execution lifecycle."""

    name: str
    paths: WorkspacePaths
    sessions_db: SessionsRepository
    no_worktree: bool = False
    keep: bool = False
    agent: str | None = None
    session_id: str | None = None
    cli_args: list[str] | None = None
    no_tty: bool = False
    auto_apply: bool = False
    env_mode: AgentEnvMode | None = None
    env_passthrough: list[str] = field(default_factory=list)
    observer: RunObserver | None = None
    failure_prompter: FailurePrompter | None = None
    warnings: list[str] = field(default_factory=list)

    def execute(self) -> BlueprintRunResult:
        """Run the full execution pipeline and return the outcome."""
        env_overrides, env_error = resolve_env_overrides(self.env_mode, self.env_passthrough)
        if env_error is not None:
            return fail(self.warnings, env_error)

        reconciliation_result = reconcile_stale_sessions(self.sessions_db, path=self.paths.root_dir)
        if reconciliation_result.warning:
            self.warnings.append(reconciliation_result.warning)

        catalog = Catalog(self.paths)
        blueprint, fail_outcome = self._load_blueprint(catalog)
        if fail_outcome is not None or blueprint is None:
            return fail_outcome or fail(self.warnings, f"Failed to load Blueprint '{self.name}'.")

        try:
            run_outcome = Engine(self.paths, db=self.sessions_db, catalog=catalog).run(
                blueprint,
                RunRequest(
                    cli_args=self.cli_args,
                    use_worktree=not self.no_worktree,
                    keep=self.keep,
                    agent=self.agent,
                    session_id=self.session_id,
                    observer=self.observer,
                    failure_prompter=self.failure_prompter,
                    no_tty=self.no_tty,
                    auto_apply=self.auto_apply,
                    env_overrides=env_overrides,
                ),
            )
        except EngineInputError as exc:
            return fail(
                self.warnings,
                format_input_error_message(
                    name=self.name,
                    result=exc.result,
                    declarations=blueprint.inputs,
                ),
            )
        except EngineRuntimeError as exc:
            return fail(self.warnings, str(exc))

        return finalize(self.sessions_db, self.warnings, run_outcome, run_outcome.session_id or "")

    def _load_blueprint(self, catalog: Catalog) -> tuple[Blueprint | None, BlueprintRunResult | None]:
        """Load and validate blueprint definition from catalog, returning error result on failure."""
        try:
            blueprint = Blueprint.load(self.name, catalog=catalog)
        except BlueprintNotFoundError as exc:
            msg = str(exc) if str(exc) else f"Blueprint '{self.name}' not found in catalog."
            return None, fail(self.warnings, msg)
        except BlueprintLoadError as exc:
            msg = str(exc) if str(exc) else "Failed to resolve blueprint."
            return None, fail(self.warnings, msg)
        except BlueprintValidationError as exc:
            msg = str(exc) if str(exc) else "Blueprint definition is invalid."
            return None, fail(self.warnings, msg)

        return blueprint, None
