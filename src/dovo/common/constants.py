# Single source of truth for every entry `dovo init` seeds under `.dovo/`
# and whether the local .gitignore ignores it. DOVO_GITIGNORE_CONTENT
# (written to .dovo/.gitignore) and DOVO_GITIGNORE_TRACKED_ENTRIES
# (surfaced by `dovo init`'s "tracking only ..." message) are both derived from
# this dict, not hand-maintained separately, so they cannot drift out of sync.
DOVO_LOCAL_GITIGNORE_ENTRIES: dict[str, bool] = {
    ".meta/": True,
    ".lock": True,
    "worktrees/": True,
    "*.db": True,
    "*.db-journal": True,
    "*.db-wal": True,
    "config.json": False,
    "project.json": False,
    "catalog/": False,
}

DOVO_GITIGNORE_CONTENT = "".join(f"{name}\n" for name, ignored in DOVO_LOCAL_GITIGNORE_ENTRIES.items() if ignored)

DOVO_GITIGNORE_TRACKED_ENTRIES = tuple(name for name, ignored in DOVO_LOCAL_GITIGNORE_ENTRIES.items() if not ignored)

BOOTSTRAP_SCHEMA_VERSION = 1

BOOTSTRAP_META_REL = ".meta/bootstrap.json"

# Wall-clock cap for internal git plumbing (worktree lifecycle, patch apply,
# mutation baseline/capture, status/diff helpers). Distinct from trigger/agent
# timeouts; prevents a hung git child from wedging ``dovo run`` indefinitely.
GIT_SUBPROCESS_TIMEOUT_SECONDS = 120

REQUIRED_SUBDIRS = (".meta",)

# Maximum diff lines rendered before truncation in interactive terminals
DEFAULT_MAX_DIFF_LINES = 500

DEFAULT_MAXIMUM_WORKTREES_ALLOWED = 3

# @TODO: Do we need a maximum?
ON_FAILURE_MAX_RETRIES_MINIMUM = 1
ON_FAILURE_MAX_RETRIES_DEFAULT = 3
ON_FAILURE_BACKOFF_MS_MINIMUM = 0
ON_FAILURE_BACKOFF_MS_DEFAULT = 0
