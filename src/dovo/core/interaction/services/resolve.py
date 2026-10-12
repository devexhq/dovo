"""Resolve an authored interaction block into a fully resolved, masked request."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from dovo.common.redact import SecretRedactor
from dovo.core.artifacts.models import ArtifactManifest
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.catalog.definitions import OPTION_VALUE_RE, CommentPolicy, InteractionSpec, OptionsFrom
from dovo.core.interaction.models import (
    PREVIEW_MAX_BYTES,
    PREVIEW_MAX_LINES,
    InteractionRequest,
    InteractionResolveResult,
    InteractionResolveStatus,
    RequestArtifact,
    RequestComment,
    RequestOption,
)


@dataclass(frozen=True)
class _LocatedArtifact:
    """Latest artifact revision located through the store: number, digest, directory and parsed manifest."""

    revision: int
    digest: str
    directory: Path
    manifest: ArtifactManifest


def resolve_request(
    step_id: str, spec: InteractionSpec, message: str, store: ArtifactStore | None, redactor: SecretRedactor
) -> InteractionResolveResult:
    """Resolve an authored interaction into a request, or a failed result carrying the ASK_* code."""
    artifact: RequestArtifact | None = None
    if spec.artifact is not None:
        located = _locate_artifact(store, spec.artifact)
        if located is None:
            return _failed(
                InteractionResolveStatus.ARTIFACT_NOT_FOUND,
                "ASK_ARTIFACT_NOT_FOUND",
                f"Artifact '{spec.artifact}' was not found (ASK_ARTIFACT_NOT_FOUND).",
            )
        artifact = RequestArtifact(
            name=spec.artifact,
            revision=located.revision,
            digest=located.digest,
            preview=_artifact_preview(located, redactor),
        )

    options: list[RequestOption] = []
    if spec.options_from is not None:
        options, failure = _options_from_artifact(spec.options_from, store, spec.comment.policy)
        if failure is not None:
            return failure
    elif spec.mode != "text":
        options = _static_options(spec)

    request = InteractionRequest(
        step_id=step_id,
        mode=spec.mode,
        message=redactor.redact_text(message),
        options=options,
        comment=RequestComment(policy=spec.comment.policy, message=spec.comment.message),
        artifact=artifact,
    )
    return _classify(request, spec)


def _classify(request: InteractionRequest, spec: InteractionSpec) -> InteractionResolveResult:
    """Return OK, NO_OPTIONS for an empty multiselect source, or OPTIONS_EMPTY for a short select source."""
    if spec.options_from is not None and not request.options and request.mode == "multiselect":
        return InteractionResolveResult(status=InteractionResolveStatus.NO_OPTIONS, request=request)
    if spec.options_from is not None and request.mode == "select" and len(request.options) < 2:
        return _failed(
            InteractionResolveStatus.OPTIONS_EMPTY,
            "ASK_OPTIONS_EMPTY",
            f"Prompt '{request.step_id}' needs at least 2 options but options_from produced "
            f"{len(request.options)} (ASK_OPTIONS_EMPTY).",
        )
    return InteractionResolveResult(status=InteractionResolveStatus.OK, request=request)


def _failed(status: InteractionResolveStatus, code: str, message: str) -> InteractionResolveResult:
    """Return a failed resolve result carrying the ASK_* code."""
    return InteractionResolveResult(status=status, errors=[message], error_code=code)


def _locate_artifact(store: ArtifactStore | None, name: str) -> _LocatedArtifact | None:
    """Return the latest revision's number, digest, directory and manifest, or None when absent."""
    if store is None:
        return None
    references = store.resolve_references({name: ("revision", "digest", "dir")}).references.get(name)
    if references is None:
        return None
    directory = Path(references["dir"])
    try:
        manifest = ArtifactManifest.model_validate_json((directory / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _LocatedArtifact(
        revision=int(references["revision"]), digest=references["digest"], directory=directory, manifest=manifest
    )


def _artifact_preview(located: _LocatedArtifact, redactor: SecretRedactor) -> list[str]:
    """Return masked preview lines: a single UTF-8 text file up to 1 MiB truncated at 200 lines, else a file/size listing."""
    lines = _text_preview_lines(located)
    if lines is None:
        lines = [f"{entry.path} ({entry.size_bytes} bytes)" for entry in located.manifest.files]
    return [redactor.redact_text(line) for line in lines]


def _text_preview_lines(located: _LocatedArtifact) -> list[str] | None:
    """Return the lines of the artifact's only file, cut at 200 with a notice, or None when it is not a small text file."""
    files = located.manifest.files
    if len(files) != 1 or files[0].size_bytes > PREVIEW_MAX_BYTES:
        return None
    try:
        text = (located.directory / files[0].path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    lines = text.splitlines()
    if len(lines) <= PREVIEW_MAX_LINES:
        return lines
    notice = f"Showing first {PREVIEW_MAX_LINES} lines of {files[0].path} ({len(lines)} total)"
    return [*lines[:PREVIEW_MAX_LINES], notice]


def _static_options(spec: InteractionSpec) -> list[RequestOption]:
    """Return the authored options, or the approve/deny defaults for an option-less confirm, with resolved comment policies."""
    default_policy = spec.comment.policy
    if spec.options is None:
        return [
            RequestOption(value="approve", label="Approve", comment=default_policy),
            RequestOption(value="deny", label="Deny", fail=True, comment=default_policy),
        ]
    return [
        RequestOption(
            value=option.value, label=option.label, fail=option.fail, comment=option.comment or default_policy
        )
        for option in spec.options
    ]


def _options_from_artifact(
    options_from: OptionsFrom, store: ArtifactStore | None, default_policy: CommentPolicy
) -> tuple[list[RequestOption], InteractionResolveResult | None]:
    """Load and validate the options_from file; return the options or a failed result."""
    located = _locate_artifact(store, options_from.artifact)
    if located is None:
        return [], _failed(
            InteractionResolveStatus.OPTIONS_ARTIFACT_NOT_FOUND,
            "ASK_OPTIONS_ARTIFACT_NOT_FOUND",
            f"Artifact '{options_from.artifact}' for options_from was not found (ASK_OPTIONS_ARTIFACT_NOT_FOUND).",
        )

    items_or_reason = _load_items(options_from, located)
    if isinstance(items_or_reason, str):
        return [], _failed(
            InteractionResolveStatus.OPTIONS_INVALID,
            "ASK_OPTIONS_INVALID",
            f"Cannot read options from '{options_from.file}' in artifact '{options_from.artifact}': "
            f"{items_or_reason} (ASK_OPTIONS_INVALID).",
        )

    options = [RequestOption(value=value, label=label, comment=default_policy) for value, label in items_or_reason]
    seen: set[str] = set()
    for option in options:
        if option.value in seen:
            return [], _failed(
                InteractionResolveStatus.OPTIONS_DUPLICATE,
                "ASK_OPTIONS_DUPLICATE",
                f"Option value '{option.value}' appears more than once (ASK_OPTIONS_DUPLICATE).",
            )
        seen.add(option.value)
    return options, None


def _load_items(options_from: OptionsFrom, located: _LocatedArtifact) -> list[tuple[str, str]] | str:
    """Return (value, label) pairs from the options file, or the reason it cannot be used."""
    if options_from.file not in {entry.path for entry in located.manifest.files}:
        return "the file is not part of the artifact"
    try:
        document = json.loads((located.directory / options_from.file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return f"cannot read JSON: {error}"

    items = _items_at_path(document, options_from.items_path)
    if items is None:
        return f"'{options_from.items_path}' is not an array"

    pairs: list[tuple[str, str]] = []
    for position, item in enumerate(items):
        if not isinstance(item, dict) or options_from.value_field not in item or options_from.label_field not in item:
            return f"item {position} needs '{options_from.value_field}' and '{options_from.label_field}'"
        value = str(item[options_from.value_field])
        if not OPTION_VALUE_RE.match(value):
            return f"item {position} has an invalid option value '{value}'"
        pairs.append((value, str(item[options_from.label_field])))
    return pairs


def _items_at_path(document: object, items_path: str) -> list[object] | None:
    """Return the JSON array at the dot path, or None when any segment is missing or the target is not a list."""
    current = document
    for segment in items_path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current if isinstance(current, list) else None
