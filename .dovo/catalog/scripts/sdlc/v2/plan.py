#!/usr/bin/env python3
"""Checks the slice plan the issue-code-v2 blueprint's planning steps write to .agentic/plan.json.

`check-questions` runs after `fold-answers`, which settles every open question
as a decision. It refuses a plan whose `open_questions` is not `[]`, or whose
`decisions` holds an entry that is not a well-formed decision (the shape is
`Decision` in plan_formats.py) or reuses another's id. A plan that passes
prints nothing and exits 0; every refusal is one line on stderr and exit code 1.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from plan_formats import Decision, FormatError

PLAN_JSON = Path(".agentic/plan.json")


class PlanError(RuntimeError):
    """Raised when a subcommand refuses; the message is the one-line reason."""


def load_plan(path: Path) -> dict[str, object]:
    """Return the parsed plan at path.

    Raises:
        PlanError: The plan cannot be read, is not valid JSON or is not a JSON object.
    """
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PlanError(f"{path} cannot be read: {error}.") from error
    if not isinstance(plan, dict):
        raise PlanError(f"{path} is not a JSON object.")
    return plan


def _list(plan: dict[str, object], key: str, path: Path) -> list[object]:
    """Return plan[key] when it is a list.

    Raises:
        PlanError: plan[key] is missing or not a list.
    """
    value = plan.get(key)
    if not isinstance(value, list):
        raise PlanError(f"{path} has no {key} list.")
    return value


def _question_id(item: object, position: int) -> str:
    """Return an open question's id, or its position (from 1) when it has none."""
    ident = item.get("id") if isinstance(item, dict) else None
    return ident if isinstance(ident, str) else f"at position {position}"


def check_questions(path: Path) -> None:
    """Refuse the plan at path unless every open question is settled as a well-formed decision.

    Raises:
        PlanError: open_questions is not [], or decisions is missing, malformed or reuses an id.
    """
    plan = load_plan(path)
    questions = _list(plan, "open_questions", path)
    if questions:
        named = ", ".join(_question_id(item, position) for position, item in enumerate(questions, start=1))
        raise PlanError(f"{path} still lists open questions {named}; fold-answers must settle each one as a decision.")
    seen: set[str] = set()
    for position, raw in enumerate(_list(plan, "decisions", path), start=1):
        try:
            decision = Decision.from_json(raw, position)
        except FormatError as error:
            raise PlanError(f"{path}: {error}") from error
        if decision.id in seen:
            raise PlanError(f"{path}: Decision {decision.id} appears twice.")
        seen.add(decision.id)


def build_parser() -> argparse.ArgumentParser:
    """Return the parser for the check-questions subcommand."""
    parser = argparse.ArgumentParser(description="Check the slice plan in .agentic/plan.json.")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-questions", help="Refuse a plan with an open question or a malformed decision.")
    check.add_argument("--plan", type=Path, default=PLAN_JSON, help="The plan.json to check.")
    return parser


def main() -> int:
    """Run one plan.py subcommand."""
    args = build_parser().parse_args()
    try:
        check_questions(args.plan)
    except PlanError as error:
        sys.stderr.write(f"plan.py: {error}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
