"""Contract tests for the Interaction entrypoint."""

from __future__ import annotations

from dovo.common.redact import SecretRedactor
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.catalog.definitions import InteractionSpec
from dovo.core.interaction import Interaction
from tests.conftest import PublishArtifact


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
