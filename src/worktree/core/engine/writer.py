"""Atomic persistence and loading of session run payloads, and run-definitions snapshotting."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from worktree.common.filesystem import Filesystem, WorkspacePaths
from worktree.core.blueprint import Blueprint
from worktree.core.blueprint.exceptions import BlueprintLoadError, BlueprintValidationError
from worktree.core.blueprint.models import BlueprintDefinition
from worktree.core.catalog import Catalog
from worktree.core.catalog.models import CatalogItemType, CatalogRecord
from worktree.core.diff.writer import get_session_dir, write_session_diff
from worktree.core.engine.exceptions import EngineSnapshotMissingError
from worktree.core.engine.models import DefinitionRef, DefinitionsManifest, SessionRunPayload
from worktree.core.step import LoopStepBlock, StepDefinition, merge_uses_step


def write_session_run_json(session_dir: Path, payload: SessionRunPayload) -> Path:
    """Atomically write run metadata and step results to run.json."""
    target_file = session_dir / "run.json"
    Filesystem.atomic_write_text(target_file, payload.model_dump_json(indent=2))
    return target_file


def load_session_run(paths: WorkspacePaths, session_id: str) -> SessionRunPayload | None:
    """Load and parse project-aware session run metadata when present and valid."""
    target_file = paths.session_dir(session_id) / "run.json"
    if not target_file.is_file():
        return None
    try:
        content = target_file.read_text(encoding="utf-8")
        return SessionRunPayload.model_validate_json(content)
    except Exception:
        return None


def _collect_uses_refs(steps: list[StepDefinition | LoopStepBlock]) -> list[str]:
    """Return the distinct uses: literal values referenced directly in steps, in first-encounter order."""
    refs: list[str] = []
    seen: set[str] = set()
    for step in steps:
        candidates = step.do if isinstance(step, LoopStepBlock) else [step]
        for candidate in candidates:
            if candidate.uses is not None and candidate.uses not in seen:
                seen.add(candidate.uses)
                refs.append(candidate.uses)
    return refs


def _resolve_one_use(catalog: Catalog, ref: str, warnings: list[str]) -> tuple[CatalogRecord, str] | None:
    """Resolve one uses: ref's catalog record and raw content, or append a warning and return None."""
    step_show = catalog.show(ref, item_type=CatalogItemType.STEP)
    if not step_show.ok or step_show.item is None or step_show.content is None:
        warnings.append(f"Failed to snapshot run definitions: catalog step '{ref}' not found.")
        return None
    return step_show.item, step_show.content


def _nested_uses_ref(content: str) -> str | None:
    """Return the parsed uses: value from a step's raw YAML content, or None when absent."""
    parsed = yaml.safe_load(content)
    nested_uses = parsed.get("uses") if isinstance(parsed, dict) else None
    return nested_uses if nested_uses else None


def _resolve_uses_chain(
    catalog: Catalog,
    blueprint: Blueprint,
    warnings: list[str],
) -> dict[str, tuple[CatalogRecord, str]] | None:
    """Breadth-first resolve every distinct uses: reference reachable from blueprint's steps, or None on the first miss."""
    visited: dict[str, tuple[CatalogRecord, str]] = {}
    queue = _collect_uses_refs(blueprint.steps)
    queued = set(queue)

    while queue:
        ref = queue.pop(0)
        if ref in visited:
            continue
        resolved = _resolve_one_use(catalog, ref, warnings)
        if resolved is None:
            return None
        visited[ref] = resolved

        nested_uses = _nested_uses_ref(resolved[1])
        if nested_uses and nested_uses not in visited and nested_uses not in queued:
            queue.append(nested_uses)
            queued.add(nested_uses)

    return visited


def _write_definitions_snapshot(
    session_dir: Path,
    blueprint_key: str,
    blueprint_content: str,
    visited: dict[str, tuple[CatalogRecord, str]],
    warnings: list[str],
) -> bool:
    """Atomically write the blueprint and every visited step's raw YAML under session_dir/definitions/."""
    try:
        Filesystem.atomic_write_text(session_dir / "definitions" / f"{blueprint_key}.yml", blueprint_content)
        for ref, (_, content) in visited.items():
            Filesystem.atomic_write_text(session_dir / "definitions" / "steps" / f"{ref}.yml", content)
    except OSError as exc:
        warnings.append(f"Failed to write run definitions snapshot: {exc}")
        return False
    return True


def _build_definitions_manifest(
    blueprint_key: str,
    blueprint_record: CatalogRecord,
    blueprint_content: str,
    visited: dict[str, tuple[CatalogRecord, str]],
    resolved_at: str,
) -> DefinitionsManifest:
    """Assemble the DefinitionsManifest from the resolved blueprint record and its visited uses: steps."""
    return DefinitionsManifest(
        blueprint=DefinitionRef(
            ref=f"{blueprint_record.tier.value}:{blueprint_record.item_type.value}:{blueprint_key}",
            sha=Filesystem.compute_checksum(blueprint_content),
            resolved_at=resolved_at,
        ),
        steps=[
            DefinitionRef(
                ref=f"{record.tier.value}:{record.item_type.value}:{ref}",
                sha=Filesystem.compute_checksum(content),
                resolved_at=resolved_at,
            )
            for ref, (record, content) in visited.items()
        ],
    )


def snapshot_definitions(
    catalog: Catalog,
    blueprint: Blueprint,
    session_dir: Path,
    warnings: list[str],
) -> DefinitionsManifest | None:
    """Snapshot the resolved blueprint and its transitive uses: step chain into session_dir/definitions/."""
    blueprint_show = catalog.show(blueprint.key, item_type=CatalogItemType.BLUEPRINT)
    if not blueprint_show.ok or blueprint_show.item is None or blueprint_show.content is None:
        warnings.append(f"Failed to snapshot run definitions: blueprint '{blueprint.key}' not found in catalog.")
        return None

    blueprint_record = blueprint_show.item
    blueprint_content = blueprint_show.content

    visited = _resolve_uses_chain(catalog, blueprint, warnings)
    if visited is None:
        return None

    if not _write_definitions_snapshot(session_dir, blueprint.key, blueprint_content, visited, warnings):
        return None

    resolved_at = datetime.now(UTC).isoformat()
    return _build_definitions_manifest(blueprint.key, blueprint_record, blueprint_content, visited, resolved_at)


def _read_snapshot_yaml(path: Path) -> dict[str, Any]:
    """Return the parsed YAML mapping at path, or raise EngineSnapshotMissingError / BlueprintLoadError."""
    if not path.is_file():
        raise EngineSnapshotMissingError(path)
    yaml_file = Filesystem.read_yaml_file(path)
    if yaml_file.error or not isinstance(yaml_file.parsed, dict):
        raise BlueprintLoadError(
            f"Failed to parse snapshot definition '{path}': {yaml_file.error or 'invalid or non-object YAML content.'}"
        )
    return yaml_file.parsed


def _resolve_step_from_snapshot(step: StepDefinition, step_snapshots: dict[str, StepDefinition]) -> StepDefinition:
    """Recursively resolve a step's uses: chain using only snapshot-sourced step definitions."""
    if step.uses is None:
        return step
    if step.uses not in step_snapshots:
        raise BlueprintValidationError(
            f"Step '{step.id}' uses snapshot step '{step.uses}' that was not captured in the session manifest."
        )

    base = step_snapshots[step.uses]
    base_resolved = _resolve_step_from_snapshot(base, step_snapshots)
    merged = merge_uses_step(step, base_resolved)
    if merged is None:
        raise BlueprintValidationError(f"Step '{step.id}' uses snapshot step '{step.uses}' that failed validation.")
    return merged


def _resolve_snapshot_steps(
    steps: list[StepDefinition | LoopStepBlock],
    step_snapshots: dict[str, StepDefinition],
) -> list[StepDefinition | LoopStepBlock]:
    """Return steps with every uses: reference replaced by its merged snapshot-resolved StepDefinition."""
    resolved: list[StepDefinition | LoopStepBlock] = []
    for step in steps:
        if isinstance(step, LoopStepBlock):
            resolved.append(
                step.model_copy(update={"do": [_resolve_step_from_snapshot(s, step_snapshots) for s in step.do]})
            )
        else:
            resolved.append(_resolve_step_from_snapshot(step, step_snapshots))
    return resolved


def load_blueprint_from_snapshot(session_dir: Path, manifest: DefinitionsManifest) -> Blueprint:
    """Rebuild a Blueprint from its session snapshot, resolving each uses: step from the snapshot's own step files."""
    _, _, key = manifest.blueprint.ref.split(":", 2)
    raw = _read_snapshot_yaml(session_dir / "definitions" / f"{key}.yml")
    definition = BlueprintDefinition.from_document(raw, key=key)

    step_snapshots: dict[str, StepDefinition] = {}
    for step_ref in manifest.steps:
        _, _, step_key = step_ref.ref.split(":", 2)
        raw_step = _read_snapshot_yaml(session_dir / "definitions" / "steps" / f"{step_key}.yml")
        try:
            step_snapshots[step_key] = StepDefinition.model_validate(raw_step)
        except ValidationError as exc:
            raise BlueprintValidationError(f"Snapshot step '{step_key}' failed validation: {exc}") from exc

    resolved_steps = _resolve_snapshot_steps(definition.steps, step_snapshots)
    return Blueprint(definition.model_copy(update={"steps": resolved_steps}), key=key)


__all__ = [
    "DefinitionRef",
    "DefinitionsManifest",
    "EngineSnapshotMissingError",
    "get_session_dir",
    "load_blueprint_from_snapshot",
    "load_session_run",
    "snapshot_definitions",
    "write_session_diff",
    "write_session_run_json",
]
