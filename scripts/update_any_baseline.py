#!/usr/bin/env python3
"""Rewrites .basedpyright/baseline.json so it holds only the explicit-Any sites that still exist.

`basedpyright --writebaseline` records every diagnostic, warnings included, and a plain run never prunes fixed
entries. This regenerates the file and keeps only `reportExplicitAny`, so warnings stay visible and the ratchet
shrinks as Any is removed. Run it after removing an `Any`; never to absorb a new one.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE = REPO_ROOT / ".basedpyright" / "baseline.json"
RULE = "reportExplicitAny"

type Baseline = dict[str, dict[str, list[dict[str, object]]]]


def _write(data: Baseline) -> None:
    """Write a baseline in basedpyright's own layout."""
    BASELINE.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")


def _count(data: Baseline) -> int:
    """Return how many entries a baseline holds."""
    return sum(len(entries) for entries in data["files"].values())


def _added(before: Baseline, after: Baseline) -> list[str]:
    """Return `file: entry` for each entry in `after` that `before` does not hold, matching entries per file."""
    added: list[str] = []
    for name, entries in after["files"].items():
        old = Counter(json.dumps(entry, sort_keys=True) for entry in before["files"].get(name, []))
        new = Counter(json.dumps(entry, sort_keys=True) for entry in entries)
        added.extend(f"{name}: {entry}" for entry in (new - old).elements())
    return added


def main() -> int:
    """Regenerate the baseline, filter it to RULE and write it only when it lost entries and gained none."""
    before: Baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    run = subprocess.run(
        ["basedpyright", "--writebaseline"], cwd=REPO_ROOT, check=False, capture_output=True, text=True
    )
    if run.returncode != 0:
        _write(before)
        sys.stderr.write(f"basedpyright --writebaseline exited {run.returncode}; baseline left unchanged\n{run.stderr}")
        return 1
    after: Baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    after["files"] = {
        name: kept
        for name, entries in after["files"].items()
        if (kept := [entry for entry in entries if entry["code"] == RULE])
    }
    added = _added(before, after)
    if added:
        _write(before)
        sys.stderr.write(
            f"refusing to add {RULE} sites to the baseline; remove the Any instead:\n" + "\n".join(added) + "\n"
        )
        return 1
    _write(after)
    sys.stdout.write(f"{RULE} baseline: {_count(before)} -> {_count(after)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
