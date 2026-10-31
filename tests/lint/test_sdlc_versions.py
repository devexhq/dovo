"""Invariant: each versioned sdlc bundle under .dovo/catalog is complete, named by its blueprints and self-contained.

A bundle is a version's prompts, scripts and tests (`tests/sdlc/<version>`). None of them names,
imports or links into another version, and a blueprint runs one version only. A file outside the
bundles and the blueprints names a version's path only when ALLOWED_OUTSIDE lists it, and every path
it names must exist, so retiring a version fails here until each such reference moves. Whether a
`frozen` version is left unchanged is a review rule, not checked here.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Final

import pytest

from tests.lint.astlib import REPO_ROOT

PROMPTS_ROOT: Final[Path] = REPO_ROOT / ".dovo" / "catalog" / "prompts" / "sdlc"
SCRIPTS_ROOT: Final[Path] = REPO_ROOT / ".dovo" / "catalog" / "scripts" / "sdlc"
TESTS_ROOT: Final[Path] = REPO_ROOT / "tests" / "sdlc"
BLUEPRINTS_DIR: Final[Path] = REPO_ROOT / ".dovo" / "catalog" / "blueprints"
VERSIONS: Final[list[Path]] = sorted(path for path in PROMPTS_ROOT.iterdir() if path.is_dir())
BLUEPRINTS: Final[list[Path]] = sorted(BLUEPRINTS_DIR.glob("*.yml"))

STATUSES: Final = frozenset({"frozen", "dev"})
SOURCE_SUFFIXES: Final = frozenset({".md", ".py", ".json"})
FIELDS: Final = frozenset({"schema_version", "version", "status", "blueprints", "scripts", "tests"})
SDLC_PATH: Final = re.compile(r"\.dovo/catalog/(?:prompts|scripts)/sdlc/(?P<version>v\d+)/[\w./-]*\w")
# Any mention of a version: a path segment (`sdlc/v2`) or a module name (`sdlc_v2_workplan`).
VERSION_REF: Final = re.compile(r"sdlc[/_](?P<version>v\d+)(?!\d)")
# A version's path as a file outside the bundles names it.
VERSION_PATH: Final = re.compile(
    r"(?:\.dovo/catalog/(?:prompts|scripts)|tests)/sdlc/(?P<version>v\d+)(?![\w.-])(?:/[\w./-]*\w)?"
)
LINK: Final = re.compile(r"\]\((?P<target>[^)\s#]+)(?:#[^)\s]*)?\)")
EXTERNAL: Final = re.compile(r"[a-z][a-z+.-]*:", re.IGNORECASE)
BUNDLE_ROOTS: Final = (PROMPTS_ROOT, SCRIPTS_ROOT, TESTS_ROOT, BLUEPRINTS_DIR)
ALLOWED_OUTSIDE: Final = frozenset(
    {".agents/skills/dovo-issue/SKILL.md", "AGENTS.md", "CLAUDE.md", "docs/agents/planning.md"}
)


def _manifest(version: Path) -> dict[str, object]:
    """Return version's parsed VERSION.json."""
    return json.loads((version / "VERSION.json").read_text(encoding="utf-8"))


def _bundle_files(version: Path) -> list[Path]:
    """Return every source file (prompt, script, manifest, test) in version's prompts, scripts and tests folders."""
    folders = (version, SCRIPTS_ROOT / version.name, TESTS_ROOT / version.name)
    return sorted(path for folder in folders for path in folder.rglob("*") if path.suffix in SOURCE_SUFFIXES)


def _sdlc_blueprints() -> list[Path]:
    """Return the blueprints that mention any sdlc version."""
    return [path for path in BLUEPRINTS if VERSION_REF.search(path.read_text(encoding="utf-8"))]


def _version_of(path: Path) -> str | None:
    """Return the sdlc version folder path lies in, or None when it lies in none."""
    for root in (PROMPTS_ROOT, SCRIPTS_ROOT, TESTS_ROOT):
        if path.is_relative_to(root) and path != root:
            return path.relative_to(root).parts[0]
    return None


def _link_targets(path: Path) -> list[Path]:
    """Return the resolved target of each relative markdown link in path."""
    text = path.read_text(encoding="utf-8")
    targets = (match["target"] for match in LINK.finditer(text))
    return [(path.parent / target).resolve() for target in targets if not EXTERNAL.match(target)]


def _outside_files() -> list[Path]:
    """Return every file git tracks or would track, outside the sdlc bundles and the blueprints."""
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    paths = (REPO_ROOT / name for name in listed)
    return [path for path in paths if path.is_file() and not any(path.is_relative_to(r) for r in BUNDLE_ROOTS)]


def _text(path: Path) -> str:
    """Return path's text, or "" when it is not UTF-8 text."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ""


class SdlcVersionTests:
    """Each sdlc version is described by its VERSION.json, used by the blueprints it lists and references no other."""

    def test_versions_exist(self) -> None:
        assert VERSIONS, f"no sdlc versions under {PROMPTS_ROOT}"
        assert BLUEPRINTS, f"no blueprints under {BLUEPRINTS_DIR}"
        assert _sdlc_blueprints(), f"no blueprint under {BLUEPRINTS_DIR} uses an sdlc version"

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

    @pytest.mark.parametrize("blueprint", _sdlc_blueprints(), ids=lambda path: path.name)
    def test_blueprint_paths_exist_and_are_listed(self, blueprint: Path) -> None:
        matches = list(SDLC_PATH.finditer(blueprint.read_text(encoding="utf-8")))
        assert matches, f"{blueprint.name} mentions an sdlc version but names none of its paths"
        for match in matches:
            assert (REPO_ROOT / match.group(0)).exists(), f"{blueprint.name} names a missing path {match.group(0)}"
            listed = _manifest(PROMPTS_ROOT / match["version"])["blueprints"]
            assert isinstance(listed, list)
            assert blueprint.stem in listed, f"{match['version']}/VERSION.json does not list {blueprint.stem}"

    @pytest.mark.parametrize("blueprint", _sdlc_blueprints(), ids=lambda path: path.name)
    def test_blueprint_uses_one_version(self, blueprint: Path) -> None:
        versions = {match["version"] for match in VERSION_REF.finditer(blueprint.read_text(encoding="utf-8"))}
        assert len(versions) == 1, f"{blueprint.name} names sdlc versions {sorted(versions)}"

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
        found = [(path, match) for path in files for match in VERSION_REF.finditer(path.read_text(encoding="utf-8"))]
        assert found, f"no file of sdlc {version.name} names its own version"
        for path, match in found:
            assert match["version"] == version.name, f"{path.relative_to(REPO_ROOT)} names {match.group(0)}"

    @pytest.mark.parametrize("version", VERSIONS, ids=lambda path: path.name)
    def test_version_links_into_no_other_version(self, version: Path) -> None:
        links = [(path, target) for path in _bundle_files(version) for target in _link_targets(path)]
        assert links, f"no file of sdlc {version.name} has a relative link"
        for path, target in links:
            assert _version_of(target) in {None, version.name}, f"{path.relative_to(REPO_ROOT)} links to {target}"

    def test_files_outside_the_bundles_name_versions_only_when_allowed(self) -> None:
        files = _outside_files()
        assert files
        naming = {path.relative_to(REPO_ROOT).as_posix(): list(VERSION_PATH.finditer(_text(path))) for path in files}
        naming = {name: matches for name, matches in naming.items() if matches}
        assert set(naming) <= ALLOWED_OUTSIDE, (
            f"name sdlc version paths without an allowance: {sorted(set(naming) - ALLOWED_OUTSIDE)}"
        )
        assert set(naming) == ALLOWED_OUTSIDE, (
            f"allowed but name no sdlc version path: {sorted(ALLOWED_OUTSIDE - set(naming))}"
        )
        for name, matches in naming.items():
            for match in matches:
                assert (REPO_ROOT / match.group(0)).exists(), f"{name} names a missing path {match.group(0)}"
