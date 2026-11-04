from abc import ABC, abstractmethod
from typing import ClassVar, TypedDict, cast

from pydantic import BaseModel, Field, JsonValue
from rich.console import RenderableType

from dovo.common.diagnostic_log import DiagnosticRecord
from dovo.common.error_codes import ErrorCode
from dovo.common.models import BaseResult, Remediation


class ResultHeader(TypedDict):
    """The six header fields every command result's payload starts with."""

    ok: bool
    error_code: ErrorCode | None
    errors: list[str]
    warnings: list[str]
    remediations: list[Remediation]
    diagnostics: list[DiagnosticRecord]


class ResultView(BaseModel):
    """Base of every view of a command result: its payload starts with the shared header, then the view's own fields."""

    model_config = {"extra": "forbid", "strict": True}

    ok: bool
    error_code: ErrorCode | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    remediations: list[Remediation] = Field(default_factory=list)
    diagnostics: list[DiagnosticRecord] = Field(default_factory=list)


def result_header(result: BaseResult) -> ResultHeader:
    """Copy the six header fields of a result, ready to splat into its ResultView."""
    return ResultHeader(
        ok=result.ok,
        error_code=result.error_code,
        errors=list(result.errors),
        warnings=list(result.warnings),
        remediations=list(result.remediations),
        diagnostics=list(result.diagnostics),
    )


class ComponentFormatter[T, V: BaseModel = BaseModel](ABC):
    """Interface for formatting a specific type of domain data.

    Rule for view models:
    A view model may contain no Rich markup and no sentence composed from fields
    it also carries separately. "[yellow]CONFIG_NOT_FOUND[/yellow]" is banned;
    code="config_not_found", severity=Severity.WARNING is required, and to_rich
    maps severity to a color. "2 valid / 2 total" is banned; valid_items=2,
    total_items=2 is required.
    """

    _STYLE_MAP: ClassVar[dict[str, str]] = {
        "success": "green",
        "warning": "yellow",
        "error": "red",
        "info": "cyan",
        "default": "default",
    }

    def transform(self, data: T) -> V:
        """Derive the presentation-ready view. Default: the model is its own view.

        Args:
            data: Domain data object to transform into a view model.

        Returns:
            The presentation view model.
        """
        # 1. Return data unchanged for the identity / no-derivation set
        return cast(V, data)

    def to_json_serializable(self, data: T) -> dict[str, JsonValue]:
        """Emit the view, so JSON and terminal cannot disagree about content.

        Args:
            data: Domain data object to convert for JSON serialization.

        Returns:
            A primitive Python dictionary matching the view's wire format.
        """
        # 1. Obtain view from transform(data)
        # 2. Return view.model_dump(mode="json") if view is BaseModel, else raise TypeError
        view = self.transform(data)
        if isinstance(view, BaseModel):
            return view.model_dump(mode="json")
        raise TypeError(f"View model must be an instance of BaseModel, got {type(view).__name__}")

    @abstractmethod
    def to_rich(self, data: T) -> RenderableType:
        """Build the renderable from transform(data). No derivation permitted here.

        Args:
            data: Domain data object to format for terminal output.

        Returns:
            A Rich renderable object.
        """
        raise NotImplementedError
