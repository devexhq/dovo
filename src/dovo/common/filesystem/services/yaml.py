from __future__ import annotations

from pathlib import Path

import yaml

from dovo.common.filesystem.models import YamlFile
from dovo.common.filesystem.services.operations import compute_content_checksum


def read_yaml_file(file_path: Path) -> YamlFile:
    """Read and parse a YAML file into a typed YamlFile model."""
    name = file_path.stem
    error = None
    yaml_data = None

    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        error = f"Failed to read catalog blueprint '{file_path}': {exc}"
        return YamlFile(name=name, path=file_path, error=error, parsed=yaml_data)

    checksum = compute_content_checksum(content)
    file_size = len(content.encode("utf-8"))

    try:
        yaml_data = yaml.safe_load(content)
        if isinstance(yaml_data, dict) and yaml_data.get("name"):
            name = str(yaml_data["name"])
    except Exception:
        pass

    return YamlFile(
        name=name,
        path=file_path,
        error=error,
        parsed=yaml_data,
        content=content,
        checksum=checksum,
        file_size=file_size,
    )


def scan_yaml_directory(
    directory: Path,
    *,
    suffixes: tuple[str, ...] = (".yml", ".yaml"),
) -> list[YamlFile]:
    """Return one entry per matching file in , sorted by name."""
    if not directory.exists():
        return []

    entries: list[YamlFile] = []
    for file_path in sorted(directory.rglob("*")):
        if not file_path.is_file() or file_path.suffix.lower() not in suffixes:
            continue
        entries.append(read_yaml_file(file_path))

    return entries
