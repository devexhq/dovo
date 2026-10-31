"""Step definition loading and shorthand resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.catalog.catalog import Catalog
from dovo.core.catalog.definitions import StepDefinition, StepType
from dovo.core.catalog.exceptions import CatalogError, StepValidationError
from dovo.core.catalog.models import CatalogItemType


def load_step(
    source: dict[str, Any] | Path | str | StepDefinition,
    *,
    paths: WorkspacePaths | None = None,
    catalog: Catalog | None = None,
) -> StepDefinition | None:
    """Load a step from a dictionary, file path, StepDefinition, or catalog step ID.

    Raises:
        CatalogError: When a file path or catalog step ID names a step that cannot be found or loaded.
    """
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
    """Resolve a catalog step by name or key via the Catalog index, or None when neither catalog nor paths is given.

    Raises:
        CatalogError: If no step matches name, or its YAML or definition is invalid.
    """
    if catalog is not None:
        cat = catalog
    elif paths is not None:
        cat = Catalog(paths)
    else:
        return None
    definition = cat.get(name, item_type=CatalogItemType.STEP, definition_cls=StepDefinition).definition
    if not isinstance(definition, StepDefinition):
        raise TypeError(f"Catalog.get returned {type(definition).__name__} for step '{name}'.")
    return definition


def load_step_by_path(path: str | Path) -> StepDefinition:
    """Load a step definition file by path.

    Raises:
        CatalogError: CATALOG_NOT_FOUND or CATALOG_SYNTAX_INVALID from read_yaml, or CATALOG_SCHEMA_INVALID
            ("Model validation failed for '<path>': <exc>") when the file fails StepDefinition validation.
    """
    try:
        return StepDefinition.model_validate(Catalog.read_yaml(Path(path)))
    except ValidationError as exc:
        raise CatalogError(ErrorCode.CATALOG_SCHEMA_INVALID, f"Model validation failed for '{path}': {exc}") from exc


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
                "artifacts": _pick("artifacts"),
                "when": using.when,
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
    """Resolve a step's 'run' or 'uses' shorthand into a concrete StepDefinition.

    Raises StepValidationError when the step cannot be resolved, and CatalogError when its 'uses'
    reference is missing, unparsable or fails validation.
    """
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
    """Resolve a step following its 'run'/'uses'/'type' mode, or None when it names no mode or has no paths for 'uses'; a bad 'uses' reference raises CatalogError."""
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
            "artifacts": step.artifacts,
            "when": step.when,
        }
    )


def _resolve_from_uses(
    step: StepDefinition,
    *,
    paths: WorkspacePaths | None = None,
    catalog: Catalog | None = None,
) -> StepDefinition | None:
    """Load the referenced step and apply only fields explicitly set in this step, raising CatalogError when it cannot be loaded."""
    if step.uses is None:
        return None
    if paths is None and catalog is None:
        return None

    base = load_step(str(step.uses), paths=paths, catalog=catalog)
    if base is None:
        return None

    base_definition = _resolve_or_none(base, paths=paths, catalog=catalog) or base
    return merge_uses_step(step, base_definition)
