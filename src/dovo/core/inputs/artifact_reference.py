"""How a condition operand rooted at 'artifacts' names an artifact, one of its fields and a path into its content."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from dovo.core.inputs.models import ArtifactReference

ARTIFACT_FIELDS: Final[frozenset[str]] = frozenset({"content", "digest", "dir", "revision"})


def _field_index(segments: Sequence[str | int]) -> int | None:
    """Return the index of the first segment after the first that names one of ARTIFACT_FIELDS, or None."""
    return next((index for index in range(1, len(segments)) if segments[index] in ARTIFACT_FIELDS), None)


def artifact_reference(segments: Sequence[str | int]) -> ArtifactReference | None:
    """Split the segments after 'artifacts' into a name, a field and a path; None when no segment after the first is a field.

    The field is the first segment after the first that names one of ARTIFACT_FIELDS; the segments before it, joined
    by '.', are the name, and the segments after it the path. A name holding an int segment is no reference.
    """
    index = _field_index(segments)
    if index is None:
        return None
    name = [segment for segment in segments[:index] if isinstance(segment, str)]
    if len(name) != index:
        return None
    return ArtifactReference(name=".".join(name), field=str(segments[index]), path=tuple(segments[index + 1 :]))
