"""Invariant: each versioned sdlc bundle under .dovo/catalog is complete, named by its blueprints and self-contained."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Final

import pytest

from tests.lint.astlib import REPO_ROOT

PROMPTS_ROOT: Final[Path] = REPO_ROOT / ".dovo" / "catalog" / "prompts" / "sdlc"
SCRIPTS_ROOT: Final[Path] = REPO_ROOT / ".dovo" / "catalog" / "scripts" / "sdlc"
BLUEPRINTS_DIR: Final[Path] = REPO_ROOT / ".dovo" / "catalog" / "blueprints"
VERSIONS: Final[list[Path]] = sorted(path for path in PROMPTS_ROOT.iterdir() if path.is_dir())
BLUEPRINTS: Final[list[Path]] = sorted(BLUEPRINTS_DIR.glob("*.yml"))

STATUSES: Final = frozenset({"frozen", "dev"})
SOURCE_SUFFIXES: Final = frozenset({".md", ".py", ".json"})
FIELDS: Final = frozenset({"schema_version", "version", "status", "blueprints", "scripts", "tests"})
SDLC_PATH: Final = re.compile(r"\.dovo/catalog/(?:prompts|scripts)/sdlc/(?P<version>v\d+)/[\w./-]*\w")


def _manifest(version: Path) -> dict[str, object]:
    """Return version's parsed VERSION.json."""
    return json.loads((version / "VERSION.json").read_text(encoding="utf-8"))


def _bundle_files(version: Path) -> list[Path]:
    """Return every source file (prompt, script, manifest) in version's prompts and scripts folders."""
    folders = (version, SCRIPTS_ROOT / version.name)
    return sorted(path for folder in folders for path in folder.rglob("*") if path.suffix in SOURCE_SUFFIXES)


class SdlcVersionTests:
    """Each sdlc version is described by its VERSION.json, used by the blueprints it lists and references no other."""

    def test_versions_exist(self) -> None:
        assert VERSIONS, f"no sdlc versions under {PROMPTS_ROOT}"
        assert BLUEPRINTS, f"no blueprints under {BLUEPRINTS_DIR}"

    @pytest.mark.parametrize("version", VERSIONS, ids=lambda path: path.name)
    def test_manifest_describes_the_version(self, version: Path) -> None:
        manifest = _manifest(version)
        assert set(manifest) == FIELDS
        assert manifest["schema_version"] == 1
        assert manifest["version"] == version.name
        assert manifest["status"] in STATUSES
        assert manifest["scripts"] == f".dovo/catalog/scripts/sdlc/{version.name}"
        assert manifest["tests"] == f"tests/sdlc/{version.name}"
        assert (REPO_ROOT / str(manifest["scripts"])).is_dir()
        assert (REPO_ROOT / str(manifest["tests"])).is_dir()

    def test_at_most_one_version_is_in_development(self) -> None:
        dev = [version.name for version in VERSIONS if _manifest(version)["status"] == "dev"]
        assert len(dev) <= 1, f"more than one sdlc version is in development: {dev}"

    @pytest.mark.parametrize("blueprint", BLUEPRINTS, ids=lambda path: path.name)
    def test_blueprint_paths_exist_and_are_listed(self, blueprint: Path) -> None:
        text = blueprint.read_text(encoding="utf-8")
        for match in SDLC_PATH.finditer(text):
            assert (REPO_ROOT / match.group(0)).exists(), f"{blueprint.name} names a missing path {match.group(0)}"
            listed = _manifest(PROMPTS_ROOT / match["version"])["blueprints"]
            assert isinstance(listed, list)
            assert blueprint.stem in listed, f"{match['version']}/VERSION.json does not list {blueprint.stem}"

    def test_every_listed_blueprint_names_the_version(self) -> None:
        for version in VERSIONS:
            listed = _manifest(version)["blueprints"]
            assert isinstance(listed, list) and listed, f"{version.name} lists no blueprints"
            for name in listed:
                text = (BLUEPRINTS_DIR / f"{name}.yml").read_text(encoding="utf-8")
                assert f"sdlc/{version.name}/" in text, f"{name}.yml does not use sdlc {version.name}"

    @pytest.mark.parametrize("version", VERSIONS, ids=lambda path: path.name)
    def test_version_references_no_other_version(self, version: Path) -> None:
        files = _bundle_files(version)
        assert files
        for path in files:
            for match in SDLC_PATH.finditer(path.read_text(encoding="utf-8")):
                assert match["version"] == version.name, f"{path.relative_to(REPO_ROOT)} names {match.group(0)}"
