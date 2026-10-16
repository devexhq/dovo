#!/usr/bin/env python3
"""Rejects a commit message without a semantic `type(scope): subject` title or with an attribution trailer.

Runs as the prek `commit-msg` hook, which passes the path of the message file as the only argument.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TYPES: tuple[str, ...] = ("build", "chore", "ci", "docs", "feat", "fix", "perf", "refactor", "revert", "style", "test")
TITLE = re.compile(rf"^({'|'.join(TYPES)})(\([\w./-]+\))?!?: \S")
EXEMPT_TITLE = re.compile(r"^(Merge |Revert \"|fixup! |squash! |amend! )")
ATTRIBUTION = re.compile(r"^\s*(co-authored-by:|.*generated with \[?claude|.*generated with cursor)", re.IGNORECASE)


def problems(message: str) -> list[str]:
    """Return why the commit message is rejected, or an empty list when it is accepted."""
    lines = [line for line in message.splitlines() if not line.startswith("#")]
    title = next((line for line in lines if line.strip()), "")
    found: list[str] = []
    if not EXEMPT_TITLE.match(title) and not TITLE.match(title):
        found.append(f"title must start with `type(scope): ` or `type: ` where type is one of {', '.join(TYPES)}")
    found.extend(f"remove the attribution line: {line.strip()}" for line in lines if ATTRIBUTION.match(line))
    return found


def main() -> int:
    """Check the message file named on the command line and exit 1 with the reasons when it is rejected."""
    found = problems(Path(sys.argv[1]).read_text(encoding="utf-8"))
    for problem in found:
        sys.stderr.write(f"commit-msg: {problem}\n")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
