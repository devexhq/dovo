from __future__ import annotations

from pathlib import Path
from typing import Any

from dovo.cli.ui.formatters.header import ResultHeaderView
from dovo.core.config.models import DovoConfig


class ConfigValidationView(ResultHeaderView):
    """Semantic view of configuration validation results."""

    config_path: Path
    raw: dict[str, Any] | None = None
    config: DovoConfig | None = None


class ConfigSetView(ResultHeaderView):
    """Semantic view of configuration mutation results."""

    config_path: Path
    key: str
    value: Any
    value_str: str
    value_type: str


class ConfigLoadView(ResultHeaderView):
    """Semantic view of configuration load results: the header, then the raw and typed config."""

    config_path: Path
    raw: dict[str, object] | None = None
    config: DovoConfig | None = None


class ConfigUnsetView(ResultHeaderView):
    """Semantic view of configuration removal results: the header, then the key and the value it held."""

    config_path: Path
    key: str
    existed: bool
    previous_value: object = None
