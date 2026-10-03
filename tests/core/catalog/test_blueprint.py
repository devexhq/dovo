from __future__ import annotations

from pathlib import Path

from worktree.common.filesystem.models import RepositoryPaths
from worktree.common.filesystem.services.global_root import resolve_global_paths
from worktree.core.catalog import Catalog
from worktree.core.catalog.blueprint import Blueprint
from worktree.core.catalog.definitions import BlueprintDefinition, StepDefinition
from worktree.core.project.services.identity import generate_project_identity, save_project_identity
from worktree.core.project.services.storage import resolve_workspace_paths


class BlueprintDocumentNormalizationTests:
    """Unit tests verifying blueprint document default normalization contracts."""

    def test_blueprint_defaults_missing_name_from_file_stem(self, tmp_path: Path) -> None:
        """Blueprint loaded without explicit 'name' inherits catalog key / file stem."""
        blueprints_dir = tmp_path / ".worktree" / "catalog" / "blueprints"
        blueprints_dir.mkdir(parents=True, exist_ok=True)
        save_project_identity(tmp_path / ".worktree" / "project.json", generate_project_identity())
        raw_yaml = "steps:\n  - id: ruff\n    run: ruff check .\n"
        (blueprints_dir / "lint-task.yml").write_text(raw_yaml, encoding="utf-8")

        paths = resolve_workspace_paths(RepositoryPaths.from_root(tmp_path), resolve_global_paths(None))
        blueprint = Blueprint.load("lint-task", catalog=Catalog(paths))

        assert blueprint.definition.name == "lint-task"
        assert len(blueprint.definition.steps) == 1
        step = blueprint.definition.steps[0]
        assert isinstance(step, StepDefinition)
        assert step.id == "ruff"
        assert step.run == "ruff check ."

    def test_blueprint_normalizes_null_description_and_summary_to_empty(self) -> None:
        """Blueprint normalizes JSON/YAML null description and summary to empty strings."""
        definition = BlueprintDefinition.from_document(
            {"name": "ship", "description": None, "summary": None},
            key="ship",
        )
        blueprint = Blueprint(definition)

        assert blueprint.definition.name == "ship"
        assert blueprint.definition.description == ""
        assert blueprint.definition.summary == ""
