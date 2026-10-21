"""Namespace provider that renders `artifacts.<name>.<field>` placeholders from the session's artifact store."""

from __future__ import annotations

from dovo.core.artifacts.store import ArtifactStore
from dovo.core.inputs import NamespaceResolutionError


def _parse_artifact_key(key: str) -> tuple[str, str] | None:
    """Parse `artifacts.<name>.<field>` into (name, field); the last segment is the field so names may contain dots."""
    if not key.startswith("artifacts."):
        return None
    parts = key.split(".")
    if len(parts) < 3:
        return None
    return ".".join(parts[1:-1]), parts[-1]


class ArtifactNamespaceProvider:
    """Resolves `artifacts.<name>.<field>` lazily against the session's latest artifact revisions."""

    namespace = "artifacts"

    def __init__(self, store: ArtifactStore | None) -> None:
        self._store = store

    def resolve(self, path: str) -> str | None:
        """Return the field value, '' for an unknown artifact, field or missing store, None for a non-artifact path.

        Raises NamespaceResolutionError carrying the ARTIFACT_CONTENT_UNAVAILABLE message when content cannot be inlined.
        """
        parsed = _parse_artifact_key(path)
        if parsed is None:
            return None

        if self._store is None:
            return ""

        name, field_name = parsed
        references = self._store.resolve_references({name: {field_name}})
        if not references.ok:
            raise NamespaceResolutionError(references.errors[0])
        return references.references.get(name, {}).get(field_name, "")
