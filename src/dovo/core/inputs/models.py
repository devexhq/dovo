"""Pydantic models for blueprint parameter inputs."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from dovo.common.models import BaseResult


class InputType(StrEnum):
    """Supported blueprint input parameter types."""

    STRING = "string"
    BOOLEAN = "boolean"
    INTEGER = "integer"


class ParameterInput(BaseModel):
    """Typed input parameter declared on a task or workflow blueprint."""

    model_config = {"extra": "forbid", "strict": True}

    type: InputType = InputType.STRING
    description: str | None = None
    required: bool = False
    default: str | int | bool | None = None
    aliases: list[str] = Field(default_factory=list)

    @field_validator("type", mode="before")
    @classmethod
    def coerce_type(cls, value: Any) -> Any:
        """Accept bare YAML strings for ``type`` under strict mode."""
        if isinstance(value, str):
            try:
                return InputType(value)
            except ValueError:
                pass
        return value

    @field_validator("aliases", mode="before")
    @classmethod
    def coerce_aliases(cls, value: Any) -> Any:
        """Normalize a single alias string into a one-element list."""
        if isinstance(value, str):
            return [value]
        return value

    @model_validator(mode="after")
    def validate_aliases(self) -> ParameterInput:
        """Reject empty alias tokens."""
        cleaned: list[str] = []
        for alias in self.aliases:
            token = alias.strip()
            if not token:
                raise ValueError("Input aliases must be non-empty strings.")
            cleaned.append(token)
        self.aliases = cleaned
        return self


class InputResolveResult(BaseResult):
    """Non-raising result of parsing and validating blueprint inputs."""

    values: dict[str, str | int | bool] = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return True when all required inputs resolved without parse errors."""
        return not self.errors and not self.missing
