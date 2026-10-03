"""Step definition loading and shorthand resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.catalog.catalog import Catalog
from dovo.core.catalog.definitions import StepDefinition, StepType
from dovo.core.catalog.exceptions import CatalogFileNotFoundError, CatalogYamlError, StepValidationError
from dovo.core.catalog.models import CatalogItemType


def load_step(
    source: dict[str, Any] | Path | str | StepDefinition,
    *,
    paths: WorkspacePaths | None = None,
    catalog: Catalog | None = None,
) -> StepDefinition | None:
    """Load a step from a dictionary, file path, StepDefinition, or catalog step ID."""
    if isinstance(source, StepDefinition):
        return source
    if isinstance(source, dict):
        try:
            return StepDefinition.model_validate(source)
        except (ValidationError, ValueError):
            return None
    if isinstance(source, Path) or (isinstance(source, str) and Path(source).is_file()):
        return load_step_by_path(Path(source))
    return load_step_by_name(str(source), paths=paths, catalog=catalog)


def load_step_by_name(
    name: str,
    *,
    paths: WorkspacePaths | None = None,
    catalog: Catalog | None = None,
) -> StepDefinition | None:
    """Resolve a catalog step by name or key via the Catalog index."""
    if catalog is not None:
        cat = catalog
    elif paths is not None:
        cat = Catalog(paths)
    else:
        return None
    result = cat.get(name, item_type=CatalogItemType.STEP, definition_cls=StepDefinition)
    if result.ok and result.definition is not None:
        return result.definition

    return None


def load_step_by_path(path: str | Path) -> StepDefinition | None:
    """Resolve a catalog item by path, returning None when it cannot be loaded."""
    try:
        return StepDefinition.model_validate(Catalog.read_yaml(Path(path)))
    except (CatalogFileNotFoundError, CatalogYamlError, ValidationError):
        return None


def merge_uses_step(using: StepDefinition, base_definition: StepDefinition) -> StepDefinition | None:
    """Merge a uses: step's explicitly-set fields onto a resolved base step definition, or None on validation failure."""
    fields_set = using.model_fields_set

    def _pick(field_name: str) -> object:
        return getattr(using, field_name) if field_name in fields_set else getattr(base_definition, field_name)

    try:
        return StepDefinition.model_validate(
            {
                "id": using.id,
                "name": _pick("name"),
                "type": base_definition.type,
                "description": _pick("description"),
                "command": base_definition.command,
                "prompt": _pick("prompt"),
                "script_path": _pick("script_path"),
                "tools": _pick("tools"),
                "env": {**base_definition.env, **using.env},
                "timeout_seconds": _pick("timeout_seconds"),
                "assert": _pick("assert_"),
                "on_failure": _pick("on_failure"),
            }
        )
    except (ValidationError, ValueError):
        return None


def resolve_step_definition(
    step: StepDefinition | dict[str, Any],
    *,
    paths: WorkspacePaths | None = None,
    catalog: Catalog | None = None,
) -> StepDefinition:
    """Resolve a step's 'run' or 'uses' shorthand into a concrete StepDefinition, raising StepValidationError when it cannot."""
    if isinstance(step, dict):
        try:
            step_definition = StepDefinition.model_validate(step)
        except (ValidationError, ValueError) as exc:
            step_id = str(step.get("id", "<unknown>"))
            raise StepValidationError(f"Step validation failed for '{step_id}': {exc}") from exc
    else:
        step_definition = step

    resolved = _resolve_or_none(step_definition, paths=paths, catalog=catalog)
    if resolved is None:
        step_id = step_definition.id
        if step_definition.uses is not None and paths is None and catalog is None:
            raise StepValidationError(f"Cannot resolve step '{step_id}' using 'uses' without workspace paths.")
        raise StepValidationError(f"Step '{step_id}' must specify one of 'run', 'uses', or 'type'.")

    return resolved


def _resolve_or_none(
    step: StepDefinition,
    *,
    paths: WorkspacePaths | None = None,
    catalog: Catalog | None = None,
) -> StepDefinition | None:
    """Resolve a step following its 'run'/'uses'/'type' mode, or None when unresolvable."""
    if step.run is not None:
        return _resolve_run(step)

    if step.uses is not None:
        return _resolve_from_uses(step, paths=paths, catalog=catalog)

    if step.type is not None:
        return step

    return None


def _resolve_run(step: StepDefinition) -> StepDefinition:
    """Expand a 'run' shorthand step into a concrete COMMAND StepDefinition."""
    return StepDefinition.model_validate(
        {
            "id": step.id,
            "name": step.name,
            "description": step.description,
            "type": StepType.COMMAND,
            "command": step.run,
            "env": step.env,
            "timeout_seconds": step.timeout_seconds,
            "assert": step.assert_,
            "on_failure": step.on_failure,
        }
    )


def _resolve_from_uses(
    step: StepDefinition,
    *,
    paths: WorkspacePaths | None = None,
    catalog: Catalog | None = None,
) -> StepDefinition | None:
    """Load the referenced step and apply only fields explicitly set in this step."""
    if step.uses is None:
        return None
    if paths is None and catalog is None:
        return None

    base = load_step(str(step.uses), paths=paths, catalog=catalog)
    if base is None:
        return None

    base_definition = _resolve_or_none(base, paths=paths, catalog=catalog) or base
    return merge_uses_step(step, base_definition)
