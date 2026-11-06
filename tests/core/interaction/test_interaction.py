"""Contract tests for the Interaction entrypoint."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.redact import SecretRedactor
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.catalog.definitions import InteractionSpec
from dovo.core.interaction import Interaction
from tests.conftest import PublishArtifact
from tests.harness.diagnostic_log import diagnostic_record


class InteractionEntrypointTests:
    """Contract tests for Interaction."""

    def test_resolve_reads_the_bound_store_and_applies_the_bound_redactor(
        self, artifact_store: ArtifactStore, publish_artifact: PublishArtifact
    ) -> None:
        publish_artifact("plan", {"PLAN.md": "old"})
        publish_artifact("plan", {"PLAN.md": "token s3cr3t-value"})
        interaction = Interaction(artifact_store, SecretRedactor([("API_TOKEN", "s3cr3t-value")]))
        spec = InteractionSpec.model_validate({"mode": "confirm", "message": "Go?", "artifact": "plan"})

        result = interaction.resolve("approve", spec, "Go?")

        assert result.request is not None
        assert result.request.artifact is not None
        assert result.request.artifact.revision == 2
        assert result.request.artifact.preview == ["token [REDACTED:API_TOKEN]"]


def _manifest_path(store: ArtifactStore, name: str) -> Path:
    """Return the manifest.json of the latest revision of artifact name."""
    return Path(store.resolve_references({name: ("dir",)}).references[name]["dir"]) / "manifest.json"


def _make_unreadable(manifest: Path) -> None:
    """Replace manifest with a directory, so reading it fails in the operating system."""
    manifest.unlink()
    manifest.mkdir()


class InteractionDamagedManifestTests:
    """Contract tests for Interaction.resolve when the located artifact's manifest is damaged."""

    @pytest.mark.parametrize(
        ("content", "message"),
        [
            pytest.param(
                b"x",
                "Artifact 'plan' has an invalid manifest: Invalid JSON: expected value at line 1 column 1.",
                id="not_json",
            ),
            pytest.param(
                b'{"name": "plan"}',
                "Artifact 'plan' has an invalid manifest: session_id: Field required.",
                id="missing_field",
            ),
            pytest.param(
                b"\xff",
                "Artifact 'plan' has a manifest that is not UTF-8 text: 'utf-8' codec can't decode byte 0xff in position 0: "
                "invalid start byte.",
                id="not_utf8",
            ),
        ],
    )
    def test_corrupt_display_manifest_reports_its_own_code_and_problem(
        self, artifact_store: ArtifactStore, publish_artifact: PublishArtifact, content: bytes, message: str
    ) -> None:
        publish_artifact("plan", {"PLAN.md": "hi"})
        _manifest_path(artifact_store, "plan").write_bytes(content)
        spec = InteractionSpec.model_validate({"mode": "confirm", "message": "Go?", "artifact": "plan"})

        result = Interaction(artifact_store, SecretRedactor([])).resolve("approve", spec, "Go?")

        assert (result.error_code, result.errors, result.diagnostics, result.request) == (
            ErrorCode.ASK_ARTIFACT_MANIFEST_UNREADABLE,
            [message],
            [],
            None,
        )

    def test_unreadable_display_manifest_names_the_os_error_and_records_it(
        self, artifact_store: ArtifactStore, publish_artifact: PublishArtifact, frozen_diagnostic_clock: Path
    ) -> None:
        publish_artifact("plan", {"PLAN.md": "hi"})
        manifest = _manifest_path(artifact_store, "plan")
        _make_unreadable(manifest)
        spec = InteractionSpec.model_validate({"mode": "confirm", "message": "Go?", "artifact": "plan"})

        result = Interaction(artifact_store, SecretRedactor([])).resolve("approve", spec, "Go?")

        assert (result.error_code, result.errors, result.diagnostics) == (
            ErrorCode.ASK_ARTIFACT_MANIFEST_UNREADABLE,
            [f"Artifact 'plan' has a manifest that cannot be read: [Errno 21] Is a directory: '{manifest}'."],
            [diagnostic_record(frozen_diagnostic_clock)],
        )

    def test_unreadable_options_from_manifest_reports_the_options_code(
        self, artifact_store: ArtifactStore, publish_artifact: PublishArtifact, frozen_diagnostic_clock: Path
    ) -> None:
        publish_artifact("review", {"review.json": "{}"})
        manifest = _manifest_path(artifact_store, "review")
        _make_unreadable(manifest)
        spec = InteractionSpec.model_validate(
            {
                "mode": "multiselect",
                "message": "Fix?",
                "options_from": {
                    "artifact": "review",
                    "file": "review.json",
                    "items_path": "findings",
                    "value_field": "id",
                    "label_field": "summary",
                },
            }
        )

        result = Interaction(artifact_store, SecretRedactor([])).resolve("triage", spec, "Fix?")

        assert (result.error_code, result.errors, result.diagnostics) == (
            ErrorCode.ASK_OPTIONS_ARTIFACT_MANIFEST_UNREADABLE,
            [f"Artifact 'review' has a manifest that cannot be read: [Errno 21] Is a directory: '{manifest}'."],
            [diagnostic_record(frozen_diagnostic_clock)],
        )
