from __future__ import annotations

from pathlib import Path

import yaml

from dovo.common.filesystem.models import YamlFile
from dovo.common.filesystem.services.operations import compute_content_checksum


def read_yaml_file(file_path: Path) -> YamlFile:
    """Read and parse a YAML file into a typed YamlFile model, with error "Failed to read '<path>': <exc>" when it cannot be read."""
    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        return YamlFile(name=file_path.stem, path=file_path, error=f"Failed to read '{file_path}': {exc}", parsed=None)
    return parse_yaml_content(file_path, content)


def parse_yaml_content(file_path: Path, content: str) -> YamlFile:
    """Parse content read from file_path into a YamlFile, named by the mapping's `name` or else the file stem."""
    name = file_path.stem
    yaml_data = None
    try:
        yaml_data = yaml.safe_load(content)
        if isinstance(yaml_data, dict) and yaml_data.get("name"):
            name = str(yaml_data["name"])
    except Exception:
        pass

    return YamlFile(
        name=name,
        path=file_path,
        error=None,
        parsed=yaml_data,
        content=content,
        checksum=compute_content_checksum(content),
        file_size=len(content.encode("utf-8")),
    )


def list_yaml_files(
    directory: Path,
    *,
    suffixes: tuple[str, ...] = (".yml", ".yaml"),
) -> list[Path]:
    """Return every file under directory, recursively, whose suffix is in suffixes, sorted by path; [] when directory is absent."""
    if not directory.exists():
        return []
    return [path for path in sorted(directory.rglob("*")) if path.is_file() and path.suffix.lower() in suffixes]
