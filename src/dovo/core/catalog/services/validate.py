"""Service for validating catalog blueprint and step definitions without executing them."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol, Self

import yaml
from pydantic import ValidationError
from pydantic_core import ErrorDetails

from dovo.common.error_codes import ErrorCode
from dovo.common.exceptions import DefinitionLoadError, DefinitionValidationError
from dovo.common.models import Remediation
from dovo.core.catalog.definitions.blueprint import STEP_KIND_TAGS
from dovo.core.catalog.exceptions import CatalogError, CatalogValidateFindings
from dovo.core.catalog.models import CatalogItemType

_PLACEHOLDER_RE = re.compile(r"\$\{\{\s*inputs\.([a-zA-Z0-9_-]+)\s*\}\}")
_PLACEHOLDER_FIELDS: Final[tuple[str, ...]] = ("run", "command", "prompt", "script_path")
_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:/")


class _PydanticModel(Protocol):
    """Minimal protocol for catalog definition classes validated via Pydantic."""

    @classmethod
    def model_validate(cls, obj: object) -> Self:
        """Validate and parse raw data into a model instance."""
        ...


@dataclass(frozen=True, slots=True)
class CheckedDefinition:
    """A catalog definition that passed the schema check and every audit, with the warnings found on the way."""

    definition: object
    warnings: tuple[str, ...]


def check_catalog_item(
    resolved_path: Path,
    item_type: str,
    key: str,
    *,
    repo_root: Path,
    definition_cls: type[_PydanticModel],
    prior_warnings: Sequence[str] = (),
) -> CheckedDefinition:
    """Read, schema-check and audit one already-resolved catalog definition file without executing it.

    This is the one check behind ``dovo blueprint validate`` and every definition a catalog lookup loads.
    prior_warnings, such as a lookup's duplicate-name warning, come first in the returned warnings and in a
    failure's findings. Each ``script_path`` is looked up under repo_root. Target resolution lives on ``Catalog``,
    not here, so this module never imports ``Catalog`` (``catalog.py`` already imports this module).

    Raises:
        CatalogError: CATALOG_FILE_UNREADABLE or CATALOG_SYNTAX_INVALID when the file cannot be read or parsed,
            and CATALOG_SCHEMA_INVALID when the schema check or an audit fails; each carries every message and
            warning in ``CatalogValidateFindings``.
    """
    warnings = tuple(prior_warnings)
    parsed = _read_and_parse_target_yaml(resolved_path, warnings)
    definition = _validate_schema(definition_cls, parsed, key, warnings)
    errors, found = _semantic_findings(item_type, definition, repo_root)
    warnings = (*warnings, *found)
    if errors:
        raise _schema_invalid(errors, _resolve_remediations(errors), warnings)
    return CheckedDefinition(definition=definition, warnings=warnings)


def _catalog_failure(
    error_code: ErrorCode, errors: Sequence[str], remediations: Sequence[Remediation], warnings: Sequence[str]
) -> CatalogError:
    """Return the check failure: the first message as its own, every message and warning as its findings."""
    return CatalogError(
        error_code,
        errors[0],
        remediations,
        data=CatalogValidateFindings(errors=tuple(errors), warnings=tuple(warnings)),
    )


def _schema_invalid(errors: list[str], remediations: list[Remediation], warnings: Sequence[str]) -> CatalogError:
    """Return the CATALOG_SCHEMA_INVALID failure carrying every message and warning."""
    return _catalog_failure(ErrorCode.CATALOG_SCHEMA_INVALID, errors, remediations, warnings)


def _read_and_parse_target_yaml(resolved_path: Path, warnings: Sequence[str]) -> dict[str, Any]:
    """Read and YAML-parse resolved_path into its top-level mapping; a failure carries warnings as its findings'.

    Raises:
        CatalogError: CATALOG_FILE_UNREADABLE when the file cannot be read, CATALOG_SYNTAX_INVALID when it is
            not valid YAML or its root is not a mapping.
    """
    try:
        content = resolved_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _catalog_failure(
            ErrorCode.CATALOG_FILE_UNREADABLE,
            [f"Failed to read '{resolved_path}': {exc}."],
            [Remediation(message=f"Check that '{resolved_path}' exists and is readable.")],
            warnings,
        ) from None

    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        location = _format_yaml_error_location(exc)
        raise _catalog_failure(
            ErrorCode.CATALOG_SYNTAX_INVALID,
            [f"YAML syntax error in '{resolved_path}' at {location}: {exc}."],
            [Remediation(message=f"Fix the YAML syntax error at {location} in '{resolved_path}'.")],
            warnings,
        ) from None

    if not isinstance(parsed, dict):
        raise _catalog_failure(
            ErrorCode.CATALOG_SYNTAX_INVALID,
            [f"'{resolved_path}' root content must be a YAML mapping."],
            [Remediation(message=f"Rewrite '{resolved_path}' with a top-level YAML mapping (key: value pairs).")],
            warnings,
        )

    return parsed


def _format_yaml_error_location(exc: yaml.YAMLError) -> str:
    """Return a human-readable 'line N, column M' phrase, or a fallback when unavailable."""
    problem_mark = getattr(exc, "problem_mark", None)
    if problem_mark is None:
        return "an unknown location"
    return f"line {problem_mark.line + 1}, column {problem_mark.column + 1}"


def _validate_schema(
    definition_cls: type[_PydanticModel], parsed: dict[str, Any], key: str, warnings: Sequence[str]
) -> object:
    """Validate parsed against definition_cls and return the definition; a failure carries warnings as its findings'.

    Raises:
        CatalogError: CATALOG_SCHEMA_INVALID with one message and remediation per schema error.
    """
    try:
        from_document = getattr(definition_cls, "from_document", None)
        return from_document(parsed, key=key) if callable(from_document) else definition_cls.model_validate(parsed)
    except (DefinitionLoadError, DefinitionValidationError, ValidationError) as exc:
        errors, remediations = _format_schema_exception(exc, key)
        raise _schema_invalid(errors, remediations, warnings) from exc


def _format_schema_exception(
    exc: DefinitionLoadError | DefinitionValidationError | ValidationError, key: str
) -> tuple[list[str], list[Remediation]]:
    """Format a schema-validation exception into (errors, remediations), one remediation per error."""
    pydantic_error = _extract_validation_error(exc)
    if pydantic_error is not None:
        messages = [_format_pydantic_error(error) for error in pydantic_error.errors()]
        return messages, [_schema_remediation(key) for _ in messages]
    return [_sentence(str(exc))], [_schema_remediation(key)]


def _sentence(text: str) -> str:
    """Return text ending in exactly one period."""
    return text if text.endswith(".") else f"{text}."


def _schema_remediation(key: str) -> Remediation:
    """Return the remediation paired with one schema error for target key."""
    return Remediation(message=f"Fix the reported schema error for target '{key}'.")


def _extract_validation_error(exc: Exception) -> ValidationError | None:
    """Return the underlying pydantic.ValidationError, checking exc and exc.__cause__."""
    if isinstance(exc, ValidationError):
        return exc
    cause = exc.__cause__
    if isinstance(cause, ValidationError):
        return cause
    return None


def _format_pydantic_error(error: ErrorDetails) -> str:
    """Format one Pydantic error entry as '<dotted.location>: <message>.', without the step-kind tag of a steps entry."""
    loc = list(error["loc"])
    if len(loc) > 2 and loc[0] == "steps" and isinstance(loc[1], int) and loc[2] in STEP_KIND_TAGS:
        del loc[2]
    location = ".".join(str(part) for part in loc) or "<root>"
    return _sentence(f"{location}: {error['msg']}")


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
    """Return one duplicate-step-id error per step id occurring more than once."""
    counts = Counter(getattr(step, "id", "") for step in flat_steps)
    return [
        f"Step id '{step_id}' is used by more than one step."
        for step_id, count in counts.items()
        if step_id and count > 1
    ]


def _audit_duplicate_input_aliases(inputs: dict[str, object]) -> list[str]:
    """Return one duplicate-alias error per alias declared by more than one input."""
    owners: dict[str, str] = {}
    errors: list[str] = []
    for input_name, input_def in inputs.items():
        for alias in getattr(input_def, "aliases", None) or []:
            owner = owners.get(alias)
            if owner is not None and owner != input_name:
                errors.append(f"Alias '{alias}' is declared by both '{owner}' and '{input_name}'.")
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
            errors.append(f"Step '{step_id}' script_path '{script_path}' is not a safe relative path.")
            continue
        if not (repo_root / script_path).exists():
            warnings.append(
                f"Step '{step_id}' script_path '{script_path}' does not exist on disk (CATALOG_SCRIPT_NOT_FOUND)."
            )
    return errors, warnings


def _semantic_findings(item_type: str, definition: object, repo_root: Path) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) from the semantic audits appropriate to item_type."""
    if item_type == CatalogItemType.STEP.value:
        return _audit_script_paths([definition], repo_root)

    steps = getattr(definition, "steps", None) or []
    inputs = getattr(definition, "inputs", None) or {}
    flat_steps = _iter_all_steps(steps)

    errors = [*_audit_duplicate_step_ids(flat_steps), *_audit_duplicate_input_aliases(inputs)]
    script_errors, warnings = _audit_script_paths(flat_steps, repo_root)
    errors.extend(script_errors)

    referenced = _collect_placeholder_references(flat_steps)
    warnings.extend(_audit_undeclared_placeholders(referenced, set(inputs)))

    return errors, warnings


def _resolve_remediations(errors: list[str]) -> list[Remediation]:
    """Return one 'Resolve: <error>' remediation per semantic error, in error order."""
    return [Remediation(message=f"Resolve: {error}") for error in errors]
