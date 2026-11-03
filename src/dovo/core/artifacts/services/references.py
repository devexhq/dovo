"""References service: resolve artifacts.<name>.<field> placeholder values from the latest published revision."""

from __future__ import annotations

from collections.abc import Collection, Mapping

from dovo.common.error_codes import ErrorCode
from dovo.core.artifacts.exceptions import ArtifactError
from dovo.core.artifacts.models import ArtifactManifest, ArtifactReferencesResult
from dovo.core.db.models import ArtifactRecord
from dovo.core.db.repositories.artifacts import ArtifactsRepository

_CONTENT_MAX_BYTES = 65536


def _unavailable(name: str, reason: str) -> ArtifactError:
    """Build the ARTIFACT_CONTENT_UNAVAILABLE error for name's content, citing reason."""
    return ArtifactError(ErrorCode.ARTIFACT_CONTENT_UNAVAILABLE, f"Artifact '{name}' cannot be inlined: {reason}")


def _content_for(name: str, record: ArtifactRecord) -> str:
    """Return the text of record's single UTF-8 file of at most 65536 bytes.

    Raises:
        ArtifactError: ARTIFACT_CONTENT_UNAVAILABLE when the content cannot be inlined; chained to the OSError when a
            stored file cannot be read.
    """
    try:
        manifest = ArtifactManifest.model_validate_json((record.path / "manifest.json").read_text(encoding="utf-8"))
        if len(manifest.files) != 1:
            raise _unavailable(name, f"artifact has {len(manifest.files)} files, content needs exactly one")
        entry = manifest.files[0]
        if entry.size_bytes > _CONTENT_MAX_BYTES:
            raise _unavailable(name, f"file is {entry.size_bytes} bytes, limit is {_CONTENT_MAX_BYTES}")
        raw = (record.path / entry.path).read_bytes()
    except OSError as exc:
        raise _unavailable(name, f"cannot read file: {exc}") from exc
    except ValueError as exc:
        raise _unavailable(name, f"cannot read file: {exc}") from None

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise _unavailable(name, "file is not valid UTF-8") from None


def _field_values(name: str, record: ArtifactRecord, fields: Collection[str]) -> dict[str, str]:
    """Return the requested field values for record, raising ArtifactError when content is requested but unavailable."""
    values: dict[str, str] = {}
    if "revision" in fields:
        values["revision"] = str(record.revision)
    if "digest" in fields:
        values["digest"] = record.digest or ""
    if "dir" in fields:
        values["dir"] = str(record.path)
    if "content" in fields:
        values["content"] = _content_for(name, record)
    return values


def resolve_artifact_references(
    db: ArtifactsRepository,
    *,
    session_id: str,
    requested: Mapping[str, Collection[str]],
) -> ArtifactReferencesResult:
    """Resolve the requested fields of each named artifact's latest revision in session_id.

    Raises:
        ArtifactError: ARTIFACT_CONTENT_UNAVAILABLE when a requested artifact's content cannot be inlined.
    """
    references: dict[str, dict[str, str]] = {}
    for name, fields in requested.items():
        record = db.get(session_id, name)
        if record is None:
            continue
        references[name] = _field_values(name, record, fields)

    return ArtifactReferencesResult(references=references)
