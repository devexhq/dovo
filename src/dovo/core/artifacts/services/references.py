"""References service: resolve artifacts.<name>.<field> placeholder values from the latest published revision."""

from __future__ import annotations

from collections.abc import Collection, Mapping

from dovo.core.artifacts.models import ArtifactManifest, ArtifactReferencesResult, ArtifactReferencesStatus
from dovo.core.db.models import ArtifactRecord
from dovo.core.db.repositories.artifacts import ArtifactsRepository

_CONTENT_MAX_BYTES = 65536


def _content_for(record: ArtifactRecord) -> tuple[str | None, str | None]:
    """Return (text, None) for a single UTF-8 file of at most 65536 bytes, else (None, reason)."""
    try:
        manifest = ArtifactManifest.model_validate_json((record.path / "manifest.json").read_text(encoding="utf-8"))
        if len(manifest.files) != 1:
            return None, f"artifact has {len(manifest.files)} files, content needs exactly one"
        entry = manifest.files[0]
        if entry.size_bytes > _CONTENT_MAX_BYTES:
            return None, f"file is {entry.size_bytes} bytes, limit is {_CONTENT_MAX_BYTES}"
        raw = (record.path / entry.path).read_bytes()
    except (OSError, ValueError) as exc:
        return None, f"cannot read file: {exc}"

    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "file is not valid UTF-8"


def _field_values(record: ArtifactRecord, fields: Collection[str]) -> tuple[dict[str, str], str | None]:
    """Return the requested field values for record, or a reason when content is requested but unavailable."""
    values: dict[str, str] = {}
    if "revision" in fields:
        values["revision"] = str(record.revision)
    if "digest" in fields:
        values["digest"] = record.digest or ""
    if "dir" in fields:
        values["dir"] = str(record.path)
    if "content" in fields:
        text, reason = _content_for(record)
        if text is None:
            return values, reason
        values["content"] = text
    return values, None


def resolve_artifact_references(
    db: ArtifactsRepository,
    *,
    session_id: str,
    requested: Mapping[str, Collection[str]],
) -> ArtifactReferencesResult:
    """Resolve the requested fields of each named artifact's latest revision in session_id."""
    references: dict[str, dict[str, str]] = {}
    for name, fields in requested.items():
        record = db.get(session_id, name)
        if record is None:
            continue

        values, reason = _field_values(record, fields)
        if reason is not None:
            return ArtifactReferencesResult(
                status=ArtifactReferencesStatus.CONTENT_UNAVAILABLE,
                errors=[f"Artifact '{name}' cannot be inlined: {reason}"],
            )
        references[name] = values

    return ArtifactReferencesResult(references=references)
