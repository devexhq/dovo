"""Blueprint handle over a catalog-loaded blueprint document."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from dovo.core.catalog.definitions import BlueprintDefinition, LoopStepBlock, StepDefinition
from dovo.core.catalog.exceptions import BlueprintLoadError, BlueprintNotFoundError
from dovo.core.catalog.models import CatalogItemType
from dovo.core.inputs import (
    InputResolveResult,
    Inputs,
    ParameterInput,
)

if TYPE_CHECKING:
    from dovo.core.catalog.catalog import Catalog


class Blueprint:
    """Load, inspect, and dump a unified task/blueprint document."""

    spec: ClassVar[type[BlueprintDefinition]] = BlueprintDefinition

    def __init__(self, instance: BlueprintDefinition, *, key: str = "", path: Path | None = None) -> None:
        self._instance = instance
        self._key = key or instance.name
        self._path = path

    @classmethod
    def load(cls, key: str, *, catalog: Catalog) -> Blueprint:
        """Load and validate one Blueprint by its globally unique catalog key.

        Raises:
            BlueprintNotFoundError: If no Blueprint record matches ``key``.
            BlueprintLoadError: If the record's YAML is unreadable or invalid.
        """
        result = catalog.get(key, item_type=CatalogItemType.BLUEPRINT, definition_cls=BlueprintDefinition)
        if not result.ok or result.definition is None or result.resolved is None:
            message = "; ".join(result.errors) or f"Blueprint '{key}' not found."
            if result.resolved is None:
                raise BlueprintNotFoundError(message)
            raise BlueprintLoadError(message)

        return cls(result.definition, key=result.resolved.key, path=result.resolved.path)

    @property
    def definition(self) -> BlueprintDefinition:
        """Return the underlying wrapped BlueprintDefinition instance."""
        return self._instance

    @property
    def name(self) -> str:
        """Return the blueprint name."""
        return self._instance.name

    @property
    def key(self) -> str:
        """Return the blueprint's globally unique catalog key."""
        return self._key

    @property
    def path(self) -> Path | None:
        """Return the blueprint's catalog-relative path, if loaded from the catalog."""
        return self._path

    @property
    def steps(self) -> list[StepDefinition | LoopStepBlock]:
        """Return the live steps list from the wrapped document."""
        return self._instance.steps

    @property
    def inputs(self) -> dict[str, ParameterInput]:
        """Return the live inputs mapping from the wrapped document."""
        return self._instance.inputs

    @property
    def use_worktree(self) -> bool:
        """Return whether the document requests a git worktree."""
        return self._instance.use_worktree

    def dump(self) -> dict[str, Any]:
        """Return the in-memory document as a JSON-mode dict, including derived kind."""
        return self._instance.model_dump(mode="json")

    def resolve_inputs(
        self,
        cli_args: list[str] | None = None,
        *,
        overrides: dict[str, str | int | bool] | None = None,
    ) -> InputResolveResult:
        """Parse CLI args against this blueprint's declared inputs."""
        return Inputs.resolve(self.inputs, cli_args=cli_args, overrides=overrides)
