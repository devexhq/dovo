"""Typer option types shared by every command that binds them."""

from __future__ import annotations

from typing import Annotated

import typer

from dovo.common.models import OutputFormatOptions

FormatOption = Annotated[
    OutputFormatOptions,
    typer.Option("--format", help="Output format: 'terminal' or 'json'."),
]
