"""Invariant: src/dovo copies a caught failure into a result only through Failure.result_fields().

Scope: the rule flags a keyword argument named `error_code` or `remediations` whose value reads the
`error_code` or `remediations` attribute of a failure. A failure is an attribute called `failure`
(`caught.failure`), or a name in the same module that is called `failure`, is bound from such an
attribute (`err = caught.failure`), or is a parameter or variable annotated with `Failure`. Every
hand-written copy into a result header sets one of the two keywords, so it catches each copy without
knowing the result's type. A site that reports a failure as a warning, a doctor check result, or a step
or agent error is not flagged, because it never passes an `error_code=` or `remediations=` keyword taken
from the failure: it may still read the code or remediations into text, as a message or a Fix: block.
Not detected: a copy written as a `**{...}` dict literal or through `model_copy(update=...)`, and a
string annotation such as `"Failure"`.
"""

from __future__ import annotations

import ast
from typing import Final

import pytest

from tests.lint.astlib import REPO_ROOT, SRC_ROOT, collect_python_files

ENTRY: Final[str] = "docs/agents/architecture/exceptions.md#failure-data-and-the-copy-into-a-result"
COPIED_FIELDS: Final[frozenset[str]] = frozenset({"error_code", "remediations"})

_ALIAS_COPY_FIXTURE: Final[str] = """
def show(caught):
    err = caught.failure
    return ShowResult(error_code=err.error_code)
"""

_ANNOTATED_COPY_FIXTURE: Final[str] = """
def build(lookup: Failure, other: Failure | None) -> ShowResult:
    found: Failure = lookup
    return ShowResult(error_code=found.error_code, remediations=list(lookup.remediations))
"""

_HAND_COPY_FIXTURE: Final[str] = """
def show(caught):
    return ShowResult(
        error_code=caught.failure.error_code,
        errors=[caught.failure.message],
    )
"""

_HELPER_COPY_FIXTURE: Final[str] = """
def build(failure):
    return ShowResult(errors=[failure.message], remediations=list(failure.remediations))
"""

_RESULT_FIELDS_FIXTURE: Final[str] = """
def show(caught):
    fields = caught.failure.result_fields()
    fields["remediations"] = [Remediation(message="Rerun it.")]
    return ShowResult(item=None, **fields)
"""

_WARNING_FIXTURE: Final[str] = """
def warn(failure):
    return CheckResult(error_code=None, warnings=[failure.message], diagnostics=error_diagnostics(failure.diagnostic))
"""


def _annotated_failure(annotation: ast.expr | None) -> bool:
    """Return whether annotation names the Failure type anywhere inside it, as in `Failure | None`."""
    return annotation is not None and any(
        isinstance(node, ast.Name) and node.id == "Failure" for node in ast.walk(annotation)
    )


def _bound_names(node: ast.AST) -> list[str]:
    """Return the names node binds to a failure: an annotated parameter or variable, or an assignment from `.failure`."""
    if isinstance(node, ast.arg) and _annotated_failure(node.annotation):
        return [node.arg]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and _annotated_failure(node.annotation):
        return [node.target.id]
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Attribute) and node.value.attr == "failure":
        return [target.id for target in node.targets if isinstance(target, ast.Name)]
    return []


def _failure_names(tree: ast.Module) -> frozenset[str]:
    """Return `failure` plus every name tree binds to a failure."""
    return frozenset({"failure", *(name for node in ast.walk(tree) for name in _bound_names(node))})


def _is_failure(node: ast.expr, names: frozenset[str]) -> bool:
    """Return whether node is a failure name or an attribute called failure."""
    return (isinstance(node, ast.Name) and node.id in names) or (
        isinstance(node, ast.Attribute) and node.attr == "failure"
    )


def _reads_failure_field(value: ast.expr, names: frozenset[str]) -> bool:
    """Return whether value reads error_code or remediations off a failure anywhere inside it."""
    return any(
        isinstance(node, ast.Attribute) and node.attr in COPIED_FIELDS and _is_failure(node.value, names)
        for node in ast.walk(value)
    )


def _hand_copies(source: str, rel_path: str) -> list[str]:
    """Return '<rel_path>:<line>: copies <field>' for each error_code= or remediations= keyword read off a failure."""
    tree = ast.parse(source, filename=rel_path)
    names = _failure_names(tree)
    return [
        f"{rel_path}:{keyword.value.lineno}: copies {keyword.arg}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg in COPIED_FIELDS and _reads_failure_field(keyword.value, names)
    ]


class FailureCopyTests:
    def test_src_copies_failures_only_through_result_fields(self) -> None:
        """Report no hand-written copy of a failure's error_code or remediations into a call in src/dovo."""
        files = collect_python_files(SRC_ROOT)
        assert files, f"{SRC_ROOT} contains no Python files, so the rule would pass vacuously"

        violations = sorted(
            copy
            for path in files
            for copy in _hand_copies(path.read_text(encoding="utf-8"), str(path.relative_to(REPO_ROOT)))
        )

        assert violations == [], f"spread failure.result_fields() into the result instead; see {ENTRY}"

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            pytest.param(_HAND_COPY_FIXTURE, ["src/dovo/fixture.py:4: copies error_code"], id="caught-failure"),
            pytest.param(_HELPER_COPY_FIXTURE, ["src/dovo/fixture.py:3: copies remediations"], id="failure-param"),
            pytest.param(_ALIAS_COPY_FIXTURE, ["src/dovo/fixture.py:4: copies error_code"], id="alias"),
            pytest.param(
                _ANNOTATED_COPY_FIXTURE,
                ["src/dovo/fixture.py:4: copies error_code", "src/dovo/fixture.py:4: copies remediations"],
                id="annotated",
            ),
            pytest.param(_RESULT_FIELDS_FIXTURE, [], id="result-fields"),
            pytest.param(_WARNING_FIXTURE, [], id="warning-only"),
        ],
    )
    def test_rule_flags_only_hand_copies(self, source: str, expected: list[str]) -> None:
        """Return expected from _hand_copies(source, 'src/dovo/fixture.py')."""
        assert _hand_copies(source, "src/dovo/fixture.py") == expected
