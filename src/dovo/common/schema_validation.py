"""Shared helpers for validating packaged JSON schemas."""

from __future__ import annotations

import json
from collections.abc import Iterable
from importlib import resources
from importlib.resources.abc import Traversable
from typing import Any

from jsonschema import Draft202012Validator, ValidationError
from pydantic import ValidationError as ModelValidationError

from dovo.common.models import BaseResult


class ValidationResult(BaseResult):
    """Outcome of validating a document against a JSON schema."""

    @property
    def ok(self) -> bool:
        """Return True if validation succeeded without errors."""
        return not self.errors


def format_error_path(path: Iterable[str | int]) -> str:
    """Render an error path with dots between keys and ``[i]`` for list indexes; an empty path renders "(root)"."""
    rendered = ""
    for part in path:
        if isinstance(part, int):
            rendered += f"[{part}]"
        else:
            rendered += f".{part}" if rendered else part
    return rendered or "(root)"


def format_validation_error(exc: ModelValidationError) -> str:
    """Render each pydantic error as "<path>: <message>" joined by "; ", never echoing the input value."""
    errors = exc.errors(include_input=False, include_url=False, include_context=False)
    return "; ".join(f"{format_error_path(error['loc'])}: {error['msg']}" for error in errors)


def _error_message(error: ValidationError) -> str:
    """Return the jsonschema message, replacing a ``pattern`` failure with "does not match pattern '<pattern>'" so the instance is never echoed."""
    if error.validator == "pattern":
        return f"does not match pattern '{error.validator_value}'"
    return error.message


class SchemaValidator:
    """Validate a document against a packaged JSON schema path."""

    def __init__(self, schema_path: Traversable, *, hide_pattern_instances: bool = False) -> None:
        self.schema_path = schema_path
        self.hide_pattern_instances = hide_pattern_instances

    def validate(self, document: dict[str, Any]) -> ValidationResult:
        """Validate ``document`` against the configured schema."""
        with self.schema_path.open(encoding="utf-8") as handle:
            schema = json.load(handle)

        validator = Draft202012Validator(
            schema,
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        )
        messages: list[str] = []
        for error in sorted(validator.iter_errors(document), key=lambda e: e.path):
            message = _error_message(error) if self.hide_pattern_instances else error.message
            messages.append(f"{format_error_path(error.path)}: {message}")
        return ValidationResult(errors=messages)


def _config_schema_path() -> Traversable:
    """Return the resource path to the bundled v1 config.json schema."""
    return resources.files("dovo.schemas.v1") / "config.json"


CONFIG_VALIDATOR = SchemaValidator(_config_schema_path(), hide_pattern_instances=True)


def _project_schema_path() -> Traversable:
    """Return the resource path to the bundled v1 project.json schema."""
    return resources.files("dovo.schemas.v1") / "project.json"


PROJECT_VALIDATOR = SchemaValidator(_project_schema_path())
