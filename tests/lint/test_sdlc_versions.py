"""Invariant: the unversioned ship bundle under .dovo/catalog is complete and self-contained, and the retired
versioned sdlc bundles (prompts, scripts and tests under an `sdlc/v<n>` folder), the blueprints that ran them,
the `dovo-plan*` skills and the `dovo:ready` label are gone and named by no tracked file.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Final

from tests.lint.astlib import REPO_ROOT

PROMPTS_ROOT: Final[Path] = REPO_ROOT / ".dovo" / "catalog" / "prompts" / "sdlc"
SCRIPTS_ROOT: Final[Path] = REPO_ROOT / ".dovo" / "catalog" / "scripts" / "sdlc"
TESTS_ROOT: Final[Path] = REPO_ROOT / "tests" / "sdlc"
BLUEPRINTS_DIR: Final[Path] = REPO_ROOT / ".dovo" / "catalog" / "blueprints"
SHIP_PROMPTS_DIR: Final[Path] = PROMPTS_ROOT / "ship"
SHIP_SCRIPTS_DIR: Final[Path] = SCRIPTS_ROOT / "ship"
SHIP_TESTS_DIR: Final[Path] = TESTS_ROOT / "ship"
SHIP_STEPS_DIR: Final[Path] = REPO_ROOT / ".dovo" / "catalog" / "steps" / "ship"
SHIP_BLUEPRINT_FILE: Final[Path] = BLUEPRINTS_DIR / "ship.yml"
SHIP_SKILL_FILE: Final[Path] = REPO_ROOT / ".agents" / "skills" / "ship" / "SKILL.md"
ISSUE_TEMPLATES_DIR: Final[Path] = REPO_ROOT / ".dovo" / "catalog" / "templates" / "issue"
SHIP_CATALOG_PATH: Final = re.compile(r"\.dovo/catalog/(?:prompts|scripts)/sdlc/ship/[\w./-]*\w")
SHIP_USES_REF: Final = re.compile(r"^\s*uses:\s*ship/(?P<step>[\w-]+)\s*$", re.MULTILINE)
EXPECTED_SHIP_PROMPTS: Final[frozenset[str]] = frozenset(
    {
        "ticket-route.md",
        "ticket-investigate.md",
        "ticket-plan.md",
        "ticket-implement.md",
        "ticket-review.md",
    }
)
EXPECTED_SHIP_STEPS: Final[frozenset[str]] = frozenset(
    {
        "prepare.yml",
        "next.yml",
        "assess.yml",
        "investigate.yml",
        "plan.yml",
        "implement.yml",
        "validate.yml",
        "review.yml",
        "record.yml",
        "publication-gate.yml",
        "publish.yml",
        "report.yml",
    }
)
EXPECTED_ISSUE_TEMPLATES: Final[frozenset[str]] = frozenset({"issue-write.md", "issue.md", "bug.md"})

SOURCE_SUFFIXES: Final = frozenset({".md", ".py", ".json"})
# Any mention of a versioned sdlc bundle: a path segment (`sdlc/v2`) or a module name (`sdlc_v2_workplan`).
VERSION_REF: Final = re.compile(r"sdlc[/_](?P<version>v\d+)(?!\d)")
VERSION_DIR: Final = re.compile(r"v\d+")
# The retired blueprints, skills and label, as whole names: `ai-feature-dev` and `smoke-feature-dev` are other names.
RETIRED_NAME: Final = re.compile(
    r"(?<![\w-])(?:feature-dev|issue-plan-v2|issue-code-v2|dovo-plan(?:-review|-challenge|-walkthrough)?)(?![\w-])"
    r"|dovo:ready"
    r"|\.dovo/catalog/steps/v\d+(?![\w.-])"
)
LINK: Final = re.compile(r"\]\((?P<target>[^)\s#]+)(?:#[^)\s]*)?\)")
EXTERNAL: Final = re.compile(r"[a-z][a-z+.-]*:", re.IGNORECASE)
# A link written as an example in a template, such as `[GH-<n>](…)`, names no file.
PLACEHOLDER: Final = re.compile(r"[<>…]")
THIS_FILE: Final[str] = Path(__file__).resolve().relative_to(REPO_ROOT).as_posix()


def _bundle_files(folder: Path) -> list[Path]:
    """Return every source file (prompt, script, manifest, test) under folder."""
    return sorted(path for path in folder.rglob("*") if path.suffix in SOURCE_SUFFIXES)


def _version_of(path: Path) -> str | None:
    """Return the sdlc bundle folder path lies in, or None when it lies in none."""
    for root in (PROMPTS_ROOT, SCRIPTS_ROOT, TESTS_ROOT):
        if path.is_relative_to(root) and path != root:
            return path.relative_to(root).parts[0]
    return None


def _link_targets(path: Path) -> list[Path]:
    """Return the resolved target of each relative markdown link in path."""
    text = path.read_text(encoding="utf-8")
    targets = (match["target"] for match in LINK.finditer(text))
    return [
        (path.parent / target).resolve()
        for target in targets
        if not EXTERNAL.match(target) and not PLACEHOLDER.search(target)
    ]


def _tracked_files() -> list[Path]:
    """Return every file git tracks or would track."""
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    return [path for path in (REPO_ROOT / name for name in listed) if path.is_file()]


def _text(path: Path) -> str:
    """Return path's text, or "" when it is not UTF-8 text."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ""


class ShipBundleTests:
    """The unversioned ship bundle is self-contained, documents stage.json contracts, and has valid links."""

    def test_ship_bundle_directories_and_skill_exist(self) -> None:
        assert SHIP_PROMPTS_DIR.is_dir()
        assert SHIP_SCRIPTS_DIR.is_dir()
        assert SHIP_TESTS_DIR.is_dir()
        assert SHIP_STEPS_DIR.is_dir()
        assert SHIP_BLUEPRINT_FILE.is_file()
        assert SHIP_SKILL_FILE.is_file()
        assert (REPO_ROOT / ".claude" / "skills" / "ship" / "SKILL.md").is_file()
        prompts = {path.name for path in SHIP_PROMPTS_DIR.glob("*.md")}
        assert prompts == EXPECTED_SHIP_PROMPTS
        steps = {path.name for path in SHIP_STEPS_DIR.glob("*.yml")}
        assert steps == EXPECTED_SHIP_STEPS

    def test_ship_steps_and_blueprint_paths_exist(self) -> None:
        blueprint_text = SHIP_BLUEPRINT_FILE.read_text(encoding="utf-8")
        used_steps = {f"{match['step']}.yml" for match in SHIP_USES_REF.finditer(blueprint_text)}
        assert used_steps == EXPECTED_SHIP_STEPS
        for step_name in sorted(EXPECTED_SHIP_STEPS):
            step_path = SHIP_STEPS_DIR / step_name
            matches = list(SHIP_CATALOG_PATH.finditer(step_path.read_text(encoding="utf-8")))
            assert matches, f"{step_name} names no ship prompt or script path"
            for match in matches:
                assert (REPO_ROOT / match.group(0)).is_file(), f"{step_name} names missing path {match.group(0)}"

    def test_ship_prompts_document_stage_envelope(self) -> None:
        required_tokens = (".agentic/ship/stage.json", '"stage"', '"disposition"', '"body_sha"', '"head_sha"')
        for prompt_name in sorted(EXPECTED_SHIP_PROMPTS):
            text = (SHIP_PROMPTS_DIR / prompt_name).read_text(encoding="utf-8")
            for token in required_tokens:
                assert token in text, f"{prompt_name} is missing required stage envelope token {token}"

    def test_ship_bundle_references_no_versioned_sdlc_bundle(self) -> None:
        ship_files = [
            *_bundle_files(SHIP_PROMPTS_DIR),
            *sorted(SHIP_STEPS_DIR.glob("*.yml")),
            SHIP_BLUEPRINT_FILE,
            SHIP_SKILL_FILE,
        ]
        offenders = [
            (path.relative_to(REPO_ROOT).as_posix(), match.group(0))
            for path in ship_files
            for match in VERSION_REF.finditer(path.read_text(encoding="utf-8"))
        ]
        assert not offenders, f"ship bundle references versioned sdlc paths: {offenders}"

    def test_ship_bundle_links_resolve_within_repository(self) -> None:
        markdown_files = [*sorted(SHIP_PROMPTS_DIR.glob("*.md")), SHIP_SKILL_FILE]
        for path in markdown_files:
            targets = _link_targets(path)
            assert targets, f"{path.relative_to(REPO_ROOT)} has no relative links"
            for target in targets:
                assert target.exists(), f"{path.relative_to(REPO_ROOT)} links to missing path {target}"
                assert target.is_relative_to(REPO_ROOT), f"{path.relative_to(REPO_ROOT)} links outside repo: {target}"
                assert _version_of(target) in {None, "ship"}, (
                    f"{path.relative_to(REPO_ROOT)} links into another sdlc bundle: {target}"
                )


class IssueTemplateTests:
    """The issue authoring templates /dovo-issue follows live under the catalog's templates folder and link to each other."""

    def test_issue_templates_exist_and_link_within_their_folder(self) -> None:
        assert {path.name for path in ISSUE_TEMPLATES_DIR.glob("*.md")} == EXPECTED_ISSUE_TEMPLATES
        for name in sorted(EXPECTED_ISSUE_TEMPLATES):
            path = ISSUE_TEMPLATES_DIR / name
            targets = _link_targets(path)
            assert targets, f"{path.relative_to(REPO_ROOT)} has no relative links"
            for target in targets:
                assert target.parent == ISSUE_TEMPLATES_DIR, f"{name} links outside the templates folder: {target}"
                assert target.is_file(), f"{name} links to missing path {target}"


class RetiredSdlcTests:
    """The versioned sdlc bundles and their blueprints, skills and label are gone, and nothing names them."""

    def test_no_versioned_sdlc_bundle_folder_remains(self) -> None:
        remaining = sorted(
            path.relative_to(REPO_ROOT).as_posix()
            for root in (PROMPTS_ROOT, SCRIPTS_ROOT, TESTS_ROOT)
            for path in root.iterdir()
            if VERSION_DIR.fullmatch(path.name)
        )
        assert not remaining, f"versioned sdlc bundle folders remain: {remaining}"

    def test_no_tracked_file_names_a_retired_bundle_blueprint_skill_or_label(self) -> None:
        files = [path for path in _tracked_files() if path.relative_to(REPO_ROOT).as_posix() != THIS_FILE]
        assert files
        naming = {
            path.relative_to(REPO_ROOT).as_posix(): sorted(
                {match.group(0) for pattern in (VERSION_REF, RETIRED_NAME) for match in pattern.finditer(_text(path))}
            )
            for path in files
        }
        offenders = {name: matches for name, matches in naming.items() if matches}
        assert not offenders, f"tracked files name retired sdlc bundles, blueprints, skills or the label: {offenders}"
