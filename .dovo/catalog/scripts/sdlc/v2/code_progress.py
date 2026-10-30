#!/usr/bin/env python3
"""Tracks which phases of a slice the issue-code blueprint's code loop has committed.

The slice's phases come from `slices[].phases` in .agentic/plan.json, in the
order the plan's Phases table lists them. .agentic/code-progress.json records
the phases already done, as `{"slice": "S3", "done": [1, 2]}`, and counts as
empty when absent. `next` names the first phase not yet done, the phase file the
breakdown wrote for it, and how many of the slice's other phases remain, as
`phase`, `phase_file` (`.agentic/phases/phase-<n>.json`, empty once every
phase is done) and `remaining`: written as
`key=value` lines to `$DOVO_OUTPUT` for later steps' templates, and printed as
one JSON object on stdout for a loop's `until`. `next` refuses when the phase
it names has no phase file, so the code loop stops rather than record a phase
no agent could implement. `check` refuses a phase the slice does not have, and
`record` adds a committed phase to `done`. Every refusal is one line on stderr
and exit code 1.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PLAN_JSON = Path(".agentic/plan.json")
PROGRESS_JSON = Path(".agentic/code-progress.json")
PHASE_FILE = ".agentic/phases/phase-{phase}.json"
ALL_DONE = "done"


class ProgressError(RuntimeError):
    """Raised when a subcommand refuses; the message is the one-line reason."""


def _load_json(path: Path) -> object:
    """Return the parsed contents of path, raising ValueError when it is not valid JSON."""
    return json.loads(path.read_text(encoding="utf-8"))


def _list(value: object) -> list[object]:
    """Return value when it is a list, otherwise an empty list."""
    return value if isinstance(value, list) else []


def slice_phases(slice_id: str) -> list[int]:
    """Return the slice's phase numbers from plan.json, in the Phases table's order.

    Raises:
        ProgressError: plan.json cannot be read, the slice is not in it, or the slice has no phases yet.
    """
    try:
        plan = _load_json(PLAN_JSON)
    except (OSError, ValueError) as error:
        raise ProgressError(f"{PLAN_JSON} cannot be read: {error}.") from error
    rows = plan.get("slices") if isinstance(plan, dict) else None
    row = next((r for r in _list(rows) if isinstance(r, dict) and r.get("id") == slice_id), None)
    if row is None:
        raise ProgressError(f"Slice {slice_id} is not in {PLAN_JSON}.")
    phases = [p for p in _list(row.get("phases")) if isinstance(p, int)]
    if not phases:
        raise ProgressError(f"Slice {slice_id} has no phases in {PLAN_JSON}; run /issue-plan --slice {slice_id} first.")
    return phases


def _progress_problem(record: object) -> str | None:
    """Return why record is not a progress record, or None when it is one."""
    if not isinstance(record, dict):
        return "it is not a JSON object"
    if not isinstance(record.get("slice"), str):
        return "it names no slice"
    done = record.get("done")
    if not isinstance(done, list) or not all(isinstance(p, int) and not isinstance(p, bool) for p in done):
        return "it has no done list of phase numbers"
    return None


def done_phases(slice_id: str) -> list[int]:
    """Return the phases code-progress.json records as done for the slice, or [] when it is absent.

    Raises:
        ProgressError: The record is not valid JSON, is malformed, or is for another slice.
    """
    if not PROGRESS_JSON.exists():
        return []
    try:
        record = _load_json(PROGRESS_JSON)
    except ValueError as error:
        raise ProgressError(f"{PROGRESS_JSON} is not a progress record: {error}.") from error
    problem = _progress_problem(record)
    if problem is not None or not isinstance(record, dict):
        raise ProgressError(f"{PROGRESS_JSON} is not a progress record: {problem}.")
    if record["slice"] != slice_id:
        raise ProgressError(f"{PROGRESS_JSON} is for slice {record['slice']}, not {slice_id}.")
    return [p for p in _list(record["done"]) if isinstance(p, int)]


def next_phase(slice_id: str) -> dict[str, object]:
    """Return the first phase of the slice not yet done, or `done`, its phase file and how many others remain.

    Raises:
        ProgressError: The phase not yet done has no phase file.
    """
    done = set(done_phases(slice_id))
    pending = [p for p in slice_phases(slice_id) if p not in done]
    if not pending:
        return {"phase": ALL_DONE, "phase_file": "", "remaining": 0}
    phase_file = PHASE_FILE.format(phase=pending[0])
    if not Path(phase_file).is_file():
        raise ProgressError(f"{phase_file} is missing; check-breakdown must pass before phase {pending[0]} is coded.")
    return {"phase": str(pending[0]), "phase_file": phase_file, "remaining": len(pending) - 1}


def check_phase(slice_id: str, phase: str) -> int:
    """Return phase as a phase number of the slice.

    Raises:
        ProgressError: phase is not one of the slice's phases.
    """
    phases = slice_phases(slice_id)
    if not (phase.isascii() and phase.isdigit()) or int(phase) not in phases:
        listed = ", ".join(str(p) for p in phases)
        raise ProgressError(f"Phase {phase} is not a phase of slice {slice_id}; its phases are {listed}.")
    return int(phase)


def record_phase(slice_id: str, phase: str) -> None:
    """Add phase to the slice's done list in code-progress.json, creating the file when it is absent."""
    number = check_phase(slice_id, phase)
    done = done_phases(slice_id)
    if number in done:
        return
    text = json.dumps({"slice": slice_id, "done": [*done, number]}) + "\n"
    staging = PROGRESS_JSON.with_name(f"{PROGRESS_JSON.name}.tmp")
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_text(text, encoding="utf-8")
    os.replace(staging, PROGRESS_JSON)


def write_next(outputs: dict[str, object]) -> None:
    """Print outputs as one JSON object, and append them as `key=value` lines to `$DOVO_OUTPUT` when it is set."""
    sys.stdout.write(json.dumps(outputs) + "\n")
    target = os.environ.get("DOVO_OUTPUT")
    if target:
        with Path(target).open("a", encoding="utf-8") as handle:
            handle.write("".join(f"{key}={value}\n" for key, value in outputs.items()))


def build_parser() -> argparse.ArgumentParser:
    """Return the parser for the next, check and record subcommands."""
    parser = argparse.ArgumentParser(description="Track the phases of a slice the code loop has committed.")
    commands = parser.add_subparsers(dest="command", required=True)
    nxt = commands.add_parser(
        "next", help="Name the first phase not yet done, its phase file and how many others remain."
    )
    check = commands.add_parser("check", help="Refuse a phase the slice does not have.")
    record = commands.add_parser("record", help="Add a committed phase to the slice's done list.")
    for sub in (nxt, check, record):
        sub.add_argument("--slice", required=True, help="The slice id, such as S3.")
    for sub in (check, record):
        sub.add_argument("--phase", required=True, help="The phase number.")
    return parser


def main() -> int:
    """Run one code_progress.py subcommand against .agentic/plan.json and .agentic/code-progress.json."""
    args = build_parser().parse_args()
    slice_id: str = args.slice
    try:
        if args.command == "next":
            write_next(next_phase(slice_id))
        elif args.command == "check":
            check_phase(slice_id, args.phase)
        else:
            record_phase(slice_id, args.phase)
    except ProgressError as error:
        sys.stderr.write(f"code_progress.py: {error}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
