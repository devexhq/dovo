"""Service for validating catalog blueprint and step definitions without executing them."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Final, Protocol

import yaml
from pydantic import ValidationError
from pydantic_core import ErrorDetails

from dovo.core.catalog.models import CatalogItemType, CatalogValidateResult, CatalogValidateStatus

_PLACEHOLDER_RE = re.compile(r"\$\{\{\s*inputs\.([a-zA-Z0-9_-]+)\s*\}\}")
_PLACEHOLDER_FIELDS: Final[tuple[str, ...]] = ("run", "command", "prompt", "script_path")
_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:/")


class _PydanticModel(Protocol):
    """Minimal protocol for catalog definition classes validated via Pydantic."""

    @classmethod
    def model_validate(cls, obj: Any) -> Any:
        """Validate and parse raw data into a model instance."""
        ...


def validate_catalog_item(
    target: str,
    *,
    repo_root: Path,
    resolved: tuple[Path, str, str, list[str]],
    blueprint_cls: type[_PydanticModel],
    step_cls: type[_PydanticModel],
) -> CatalogValidateResult:
    """Validate one already-resolved catalog blueprint or step definition file, without executing it.

    ``resolved`` (``resolved_path``, ``item_type``, ``key``, ``resolution_warnings``) comes from
    ``Catalog._resolve_validate_target``: target resolution lives on ``Catalog`` itself, not here, so
    this module never needs to import ``Catalog`` (which would create an import cycle, since ``catalog.py``
    already imports this module).
    """
    resolved_path, item_type, key, resolution_warnings = resolved
    result = _validate_resolved_target(
        target, repo_root, resolved_path, item_type, key, blueprint_cls=blueprint_cls, step_cls=step_cls
    )
    if not resolution_warnings:
        return result
    return result.model_copy(update={"warnings": [*resolution_warnings, *result.warnings]})


def _validate_resolved_target(
    target: str,
    repo_root: Path,
    resolved_path: Path,
    item_type: str,
    key: str,
    *,
    blueprint_cls: type[_PydanticModel],
    step_cls: type[_PydanticModel],
) -> CatalogValidateResult:
    """Read, schema-validate, and semantically audit an already-resolved catalog definition file."""
    parse_outcome = _read_and_parse_target_yaml(target, resolved_path, item_type)
    if isinstance(parse_outcome, CatalogValidateResult):
        return parse_outcome

    definition, schema_errors, schema_fixes = _validate_schema(
        item_type, parse_outcome, key, blueprint_cls=blueprint_cls, step_cls=step_cls
    )
    if schema_errors:
        return CatalogValidateResult(
            status=CatalogValidateStatus.INVALID,
            valid=False,
            target=target,
            resolved_path=resolved_path,
            item_type=item_type,
            errors=schema_errors,
            warnings=[],
            fixes=schema_fixes,
        )

    semantic_errors, semantic_fixes, warnings = _run_semantic_audits(item_type, definition, repo_root)
    if semantic_errors:
        return CatalogValidateResult(
            status=CatalogValidateStatus.INVALID,
            valid=False,
            target=target,
            resolved_path=resolved_path,
            item_type=item_type,
            errors=semantic_errors,
            warnings=warnings,
            fixes=semantic_fixes,
        )

    return CatalogValidateResult(
        status=CatalogValidateStatus.OK,
        valid=True,
        target=target,
        resolved_path=resolved_path,
        item_type=item_type,
        errors=[],
        warnings=warnings,
        fixes=[],
    )


def _read_and_parse_target_yaml(
    target: str,
    resolved_path: Path,
    item_type: str,
) -> CatalogValidateResult | dict[str, Any]:
    """Read and YAML-parse resolved_path, or return a terminal unreadable/syntax_error/invalid-root result."""
    try:
        content = resolved_path.read_text(encoding="utf-8")
    except OSError as exc:
        return CatalogValidateResult(
            status=CatalogValidateStatus.UNREADABLE,
            valid=False,
            target=target,
            resolved_path=resolved_path,
            item_type=item_type,
            errors=[f"Failed to read '{resolved_path}': {exc} (CATALOG_FILE_UNREADABLE)."],
            warnings=[],
            fixes=[f"Check that '{resolved_path}' exists and is readable."],
        )

    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        location = _format_yaml_error_location(exc)
        return CatalogValidateResult(
            status=CatalogValidateStatus.SYNTAX_ERROR,
            valid=False,
            target=target,
            resolved_path=resolved_path,
            item_type=item_type,
            errors=[f"YAML syntax error in '{resolved_path}' at {location}: {exc} (CATALOG_YAML_SYNTAX_ERROR)."],
            warnings=[],
            fixes=[f"Fix the YAML syntax error at {location} in '{resolved_path}'."],
        )

    if not isinstance(parsed, dict):
        return CatalogValidateResult(
            status=CatalogValidateStatus.INVALID,
            valid=False,
            target=target,
            resolved_path=resolved_path,
            item_type=item_type,
            errors=[f"'{resolved_path}' root content must be a YAML mapping (CATALOG_ROOT_NOT_OBJECT)."],
            warnings=[],
            fixes=[f"Rewrite '{resolved_path}' with a top-level YAML mapping (key: value pairs)."],
        )

    return parsed


def _format_yaml_error_location(exc: yaml.YAMLError) -> str:
    """Return a human-readable 'line N, column M' phrase, or a fallback when unavailable."""
    problem_mark = getattr(exc, "problem_mark", None)
    if problem_mark is None:
        return "an unknown location"
    return f"line {problem_mark.line + 1}, column {problem_mark.column + 1}"


def _validate_schema(
    item_type: str,
    parsed: dict[str, Any],
    key: str,
    *,
    blueprint_cls: type[_PydanticModel],
    step_cls: type[_PydanticModel],
) -> tuple[object | None, list[str], list[str]]:
    """Validate parsed against the already-known schema class for item_type; return (definition, errors, fixes)."""
    definition_cls = step_cls if item_type == CatalogItemType.STEP.value else blueprint_cls
    try:
        from_document = getattr(definition_cls, "from_document", None)
        definition = (
            from_document(parsed, key=key) if callable(from_document) else definition_cls.model_validate(parsed)
        )
    except Exception as exc:
        errors, fixes = _format_schema_exception(exc, key)
        return None, errors, fixes

    return definition, [], []


def _format_schema_exception(exc: Exception, key: str) -> tuple[list[str], list[str]]:
    """Format a schema-validation exception into (errors, fixes)."""
    pydantic_error = _extract_validation_error(exc)
    if pydantic_error is not None:
        messages = [_format_pydantic_error(error) for error in pydantic_error.errors()]
        return messages, [f"Fix the reported schema error for target '{key}'." for _ in messages]
    return [f"{exc} (CATALOG_SCHEMA_INVALID)."], [f"Fix the reported schema error for target '{key}'."]


def _extract_validation_error(exc: Exception) -> ValidationError | None:
    """Return the underlying pydantic.ValidationError, checking exc and exc.__cause__."""
    if isinstance(exc, ValidationError):
        return exc
    cause = exc.__cause__
    if isinstance(cause, ValidationError):
        return cause
    return None


def _format_pydantic_error(error: ErrorDetails) -> str:
    """Format one Pydantic error entry as '<dotted.location>: <message> (CATALOG_SCHEMA_INVALID).'."""
    location = ".".join(str(part) for part in error["loc"]) or "<root>"
    return f"{location}: {error['msg']} (CATALOG_SCHEMA_INVALID)."


def _iter_all_steps(steps: list[object]) -> list[object]:
    """Flatten top-level steps and any nested loop 'do' steps into one list, duck-typed."""
    flattened: list[object] = []
    for step in steps:
        flattened.append(step)
        nested = getattr(step, "do", None)
        if isinstance(nested, list):
            flattened.extend(_iter_all_steps(nested))
    return flattened


def _audit_duplicate_step_ids(flat_steps: list[object]) -> list[str]:
    """Return one CATALOG_DUPLICATE_STEP_ID error per step id occurring more than once."""
    counts = Counter(getattr(step, "id", "") for step in flat_steps)
    return [
        f"Step id '{step_id}' is used by more than one step (CATALOG_DUPLICATE_STEP_ID)."
        for step_id, count in counts.items()
        if step_id and count > 1
    ]


def _audit_duplicate_input_aliases(inputs: dict[str, object]) -> list[str]:
    """Return one CATALOG_DUPLICATE_INPUT_ALIAS error per alias declared by more than one input."""
    owners: dict[str, str] = {}
    errors: list[str] = []
    for input_name, input_def in inputs.items():
        for alias in getattr(input_def, "aliases", None) or []:
            owner = owners.get(alias)
            if owner is not None and owner != input_name:
                errors.append(
                    f"Alias '{alias}' is declared by both '{owner}' and '{input_name}' (CATALOG_DUPLICATE_INPUT_ALIAS)."
                )
            else:
                owners[alias] = input_name
    return errors


def _collect_field_placeholder_names(value: object) -> set[str]:
    """Return placeholder input names referenced by value, if it is a string."""
    return set(_PLACEHOLDER_RE.findall(value)) if isinstance(value, str) else set()


def _collect_step_placeholder_references(step: object) -> set[str]:
    """Return the set of `inputs.<name>` placeholder names referenced by one step's own fields."""
    referenced: set[str] = set()
    for field_name in _PLACEHOLDER_FIELDS:
        referenced.update(_collect_field_placeholder_names(getattr(step, field_name, None)))
    env = getattr(step, "env", None)
    if isinstance(env, dict):
        for value in env.values():
            referenced.update(_collect_field_placeholder_names(value))
    return referenced


def _collect_placeholder_references(flat_steps: list[object]) -> set[str]:
    """Return the set of `inputs.<name>` placeholder names referenced across run/command/prompt/script_path/env."""
    referenced: set[str] = set()
    for step in flat_steps:
        referenced.update(_collect_step_placeholder_references(step))
    return referenced


def _audit_undeclared_placeholders(referenced: set[str], declared_inputs: set[str]) -> list[str]:
    """Return one CATALOG_UNDECLARED_INPUT_PLACEHOLDER warning per referenced-but-undeclared input name."""
    return [
        f"Placeholder 'inputs.{name}' is not declared in 'inputs:' (CATALOG_UNDECLARED_INPUT_PLACEHOLDER)."
        for name in sorted(referenced - declared_inputs)
    ]


def _is_unsafe_relative_path(value: str) -> bool:
    """Return True when value is empty, absolute, or contains a '..' path segment."""
    normalized = value.replace("\\", "/")
    if not normalized or normalized.startswith("/") or _DRIVE_PATH_RE.match(normalized):
        return True
    return any(part == ".." for part in normalized.split("/"))


def _audit_script_paths(flat_steps: list[object], repo_root: Path) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for type=script steps: unsafe script_path is an error, missing-on-disk is a warning."""
    errors: list[str] = []
    warnings: list[str] = []
    for step in flat_steps:
        if getattr(step, "type", None) != "script":
            continue
        script_path = getattr(step, "script_path", None)
        if not script_path:
            continue
        step_id = getattr(step, "id", "")
        if _is_unsafe_relative_path(script_path):
            errors.append(
                f"Step '{step_id}' script_path '{script_path}' is not a safe relative path (CATALOG_UNSAFE_SCRIPT_PATH)."
            )
            continue
        if not (repo_root / script_path).exists():
            warnings.append(
                f"Step '{step_id}' script_path '{script_path}' does not exist on disk (CATALOG_SCRIPT_NOT_FOUND)."
            )
    return errors, warnings


def _run_semantic_audits(item_type: str, definition: object, repo_root: Path) -> tuple[list[str], list[str], list[str]]:
    """Run the FR-4 + Scope semantic audits appropriate to item_type; return (errors, fixes, warnings)."""
    if item_type == CatalogItemType.STEP.value:
        script_errors, script_warnings = _audit_script_paths([definition], repo_root)
        fixes = [f"Resolve: {error}" for error in script_errors]
        return script_errors, fixes, script_warnings

    steps = getattr(definition, "steps", None) or []
    inputs = getattr(definition, "inputs", None) or {}
    flat_steps = _iter_all_steps(steps)

    errors = [*_audit_duplicate_step_ids(flat_steps), *_audit_duplicate_input_aliases(inputs)]
    script_errors, warnings = _audit_script_paths(flat_steps, repo_root)
    errors.extend(script_errors)

    referenced = _collect_placeholder_references(flat_steps)
    warnings.extend(_audit_undeclared_placeholders(referenced, set(inputs)))

    fixes = [f"Resolve: {error}" for error in errors]
    return errors, fixes, warnings
