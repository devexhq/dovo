"""Bootstrap domain package."""

from dovo.common.constants import (
    BOOTSTRAP_META_REL,
    BOOTSTRAP_SCHEMA_VERSION,
    REQUIRED_SUBDIRS,
)
from dovo.core.bootstrap.exceptions import BootstrapError
from dovo.core.bootstrap.models import (
    BootstrapOutcome,
    BootstrapSummary,
    DirEnsureOutcome,
    WorkspaceInitResult,
)
from dovo.core.bootstrap.services.bootstrap import (
    assert_writable,
    bootstrap_dovo,
    ensure_dir,
    load_existing_bootstrap_metadata,
    write_bootstrap_metadata,
)
from dovo.core.bootstrap.services.initialize import auto_initialize_workspace, initialize_workspace

__all__ = [
    "BOOTSTRAP_META_REL",
    "BOOTSTRAP_SCHEMA_VERSION",
    "REQUIRED_SUBDIRS",
    "BootstrapError",
    "BootstrapOutcome",
    "BootstrapSummary",
    "DirEnsureOutcome",
    "WorkspaceInitResult",
    "assert_writable",
    "auto_initialize_workspace",
    "bootstrap_dovo",
    "ensure_dir",
    "initialize_workspace",
    "load_existing_bootstrap_metadata",
    "write_bootstrap_metadata",
]
