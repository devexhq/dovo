"""Dovo CLI package initialization."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("devexhq-dovo")
    # For a CLI, you can expose this to your argument parser (e.g., Click, Typer, or argparse)
except PackageNotFoundError:
    # Occurs only if running the source code directly without installing it first
    __version__ = "0.0.0-dev"
