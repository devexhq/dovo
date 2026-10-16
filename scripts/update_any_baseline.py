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
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE = REPO_ROOT / ".basedpyright" / "baseline.json"
RULE = "reportExplicitAny"


def main() -> int:
    """Regenerate the baseline, filter it to RULE and report how many sites remain."""
    before = json.loads(BASELINE.read_text(encoding="utf-8"))
    count_before = sum(len(entries) for entries in before["files"].values())
    subprocess.run(["basedpyright", "--writebaseline"], cwd=REPO_ROOT, check=False, capture_output=True)
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    data["files"] = {
        name: kept
        for name, entries in data["files"].items()
        if (kept := [entry for entry in entries if entry["code"] == RULE])
    }
    count_after = sum(len(entries) for entries in data["files"].values())
    if count_after > count_before:
        sys.stderr.write(f"refusing to grow the {RULE} baseline from {count_before} to {count_after}\n")
        BASELINE.write_text(json.dumps(before, indent=4) + "\n", encoding="utf-8")
        return 1
    BASELINE.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")
    sys.stdout.write(f"{RULE} baseline: {count_before} -> {count_after}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
