from abc import ABC, abstractmethod
from typing import Any, ClassVar, cast

from pydantic import BaseModel


class ComponentFormatter[T, V: BaseModel = Any](ABC):
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

    def to_json_serializable(self, data: T) -> dict[str, Any]:
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

    def to_raw(self, data: T) -> str:
        """Render as raw text.

        Args:
            data: Domain data object to render to terminal output.

        Returns:
            A raw text string.
        """
        raise NotImplementedError

    @abstractmethod
    def to_rich(self, data: T) -> Any:
        """Build the renderable from transform(data). No derivation permitted here.

        Args:
            data: Domain data object to format for terminal output.

        Returns:
            A Rich renderable object.
        """
        raise NotImplementedError
