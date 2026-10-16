"""Invariant: every model in src/dovo that sets model_config validates strictly, except the allowlisted ones."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import pytest

from tests.lint.astlib import REPO_ROOT, SRC_ROOT, collect_python_files

# Models that set model_config without strict=True: (file, class). Entries are only ever removed, never added.
STRICTNESS_EXCEPTIONS: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        # Models of hand-authored blueprint YAML that predate the strict default.
        ("src/dovo/common/models.py", "BlueprintDefaults"),
        ("src/dovo/core/catalog/definitions/blueprint.py", "BlueprintDefaults"),
        ("src/dovo/core/catalog/definitions/blueprint.py", "BlueprintDefinition"),
        ("src/dovo/core/catalog/definitions/step.py", "LoopStepBlock"),
        # SQLModel table records that predate the strict default.
        ("src/dovo/core/db/models.py", "WorktreeRecord"),
        ("src/dovo/core/db/models.py", "SessionRecord"),
        ("src/dovo/core/db/models.py", "ArtifactRecord"),
        ("src/dovo/core/db/models.py", "CostRecord"),
    }
)


def _is_strict(config: ast.expr | None) -> bool:
    """Return whether a model_config value sets strict to True, as a ConfigDict call or a dict literal."""
    if isinstance(config, ast.Call):
        return any(
            kw.arg == "strict" and isinstance(kw.value, ast.Constant) and kw.value.value is True
            for kw in config.keywords
        )
    if isinstance(config, ast.Dict):
        return any(
            isinstance(key, ast.Constant)
            and key.value == "strict"
            and isinstance(value, ast.Constant)
            and value.value is True
            for key, value in zip(config.keys, config.values, strict=True)
        )
    return False


def _model_config(node: ast.ClassDef) -> ast.expr | None:
    """Return the value assigned to model_config in a class body, or None when the class does not set it."""
    for statement in node.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "model_config" for target in statement.targets
        ):
            return statement.value
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            if statement.target.id == "model_config":
                return statement.value
    return None


def _non_strict_models(file_path: Path) -> set[tuple[str, str]]:
    """Return (file, class) for each class in one module that sets model_config without strict=True."""
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    rel_path = str(file_path.relative_to(REPO_ROOT))
    return {
        (rel_path, node.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and (config := _model_config(node)) is not None and not _is_strict(config)
    }


class ModelStrictnessTests:
    def test_models_set_strict(self) -> None:
        files = collect_python_files(SRC_ROOT)
        assert files, f"{SRC_ROOT} contains no Python files, so the rule would pass vacuously"

        violations = sorted(model for path in files for model in _non_strict_models(path) - STRICTNESS_EXCEPTIONS)

        assert violations == [], "add strict=True to model_config"

    @pytest.mark.parametrize(
        "exception", [pytest.param(item, id=f"{item[0]}::{item[1]}") for item in sorted(STRICTNESS_EXCEPTIONS)]
    )
    def test_strictness_exception_still_occurs(self, exception: tuple[str, str]) -> None:
        """Require every allowlisted model to still be non-strict, so a fixed one is removed from the list."""
        assert exception in _non_strict_models(REPO_ROOT / exception[0]), "delete it from STRICTNESS_EXCEPTIONS"
