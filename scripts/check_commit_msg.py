#!/usr/bin/env python3
"""Rejects a commit message without a semantic `type(scope): subject` title.

Runs as the prek `commit-msg` hook, which passes the path of the message file as the only argument.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TYPES: tuple[str, ...] = ("build", "chore", "ci", "docs", "feat", "fix", "perf", "refactor", "revert", "style", "test")
TITLE = re.compile(rf"^({'|'.join(TYPES)})(\([\w./-]+\))?!?: \S")
EXEMPT_TITLE = re.compile(r"^(Merge |Revert \"|fixup! |squash! |amend! )")


def problems(message: str) -> list[str]:
    """Return why the commit message is rejected, or an empty list when it is accepted."""
    lines = [line for line in message.splitlines() if not line.startswith("#")]
    title = next((line for line in lines if line.strip()), "")
    if EXEMPT_TITLE.match(title) or TITLE.match(title):
        return []
    return [f"title must start with `type(scope): ` or `type: ` where type is one of {', '.join(TYPES)}"]


def main() -> int:
    """Check the message file named on the command line and exit 1 with the reasons when it is rejected."""
    found = problems(Path(sys.argv[1]).read_text(encoding="utf-8"))
    for problem in found:
        sys.stderr.write(f"commit-msg: {problem}\n")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
