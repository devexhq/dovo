"""Namespace provider that renders `artifacts.<name>.<field>` placeholders from the session's artifact store."""

from __future__ import annotations

from dovo.core.artifacts.store import ArtifactStore
from dovo.core.expressions import ABSENT, Path
from dovo.core.inputs import UNDEFINED, NamespaceResolutionError


class ArtifactNamespaceProvider:
    """Resolves `artifacts.<name>.<field>` lazily against the session's latest artifact revisions."""

    namespace = "artifacts"

    def __init__(self, store: ArtifactStore | None) -> None:
        self._store = store

    def resolve(self, path: Path) -> object:
        """Return the field value, ABSENT for an unknown artifact, field or missing store, UNDEFINED for a non-artifact path.

        The last segment is the field and the others, joined by '.', the name, so names may contain dots.
        Raises NamespaceResolutionError carrying the cannot-be-inlined message when content cannot be inlined.
        """
        segments = path.segments
        if path.root != "artifacts" or len(segments) < 2 or not all(isinstance(s, str) for s in segments):
            return UNDEFINED

        if self._store is None:
            return ABSENT

        name, field_name = ".".join(map(str, segments[:-1])), str(segments[-1])
        references = self._store.resolve_references({name: {field_name}})
        if not references.ok:
            raise NamespaceResolutionError(references.errors[0])
        return references.references.get(name, {}).get(field_name, ABSENT)
