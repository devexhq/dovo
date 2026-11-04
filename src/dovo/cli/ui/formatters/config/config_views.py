from __future__ import annotations

from pathlib import Path
from typing import Any

from dovo.cli.ui.formatters.base import ResultView
from dovo.core.config.models import DovoConfig


class ConfigValidationView(ResultView):
    """Semantic view of configuration validation results."""

    config_path: Path
    raw: dict[str, Any] | None = None
    config: DovoConfig | None = None


class ConfigSetView(ResultView):
    """Semantic view of configuration mutation results."""

    config_path: Path
    key: str
    value: Any
    value_str: str
    value_type: str
