"""Unified-diff validation (no git apply)."""

from dovo.core.patch.exceptions import MalformedDiffHeader
from dovo.core.patch.models import PatchApplyResult, PatchApplyStatus
from dovo.core.patch.patch import GitDiffParser, validate_patch_text

__all__ = [
    "GitDiffParser",
    "MalformedDiffHeader",
    "PatchApplyResult",
    "PatchApplyStatus",
    "validate_patch_text",
]
