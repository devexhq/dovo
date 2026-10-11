"""Contract tests pinning the bundled `dovo/upload-artifact` and `dovo/download-artifact` catalog step templates."""

from __future__ import annotations

import pytest
import yaml

from dovo.common.filesystem import Filesystem
from dovo.core.catalog.definitions import StepDefinition, StepType


class UploadArtifactCatalogStepValidationTests:
    """[tier-1/unit] Catalog validation: `steps/dovo/upload-artifact.yml` parses as a type=internal StepDefinition."""

    def test_upload_artifact_template_validates_as_step_definition(self) -> None:
        """[tier-1/unit] Catalog validation: `steps/dovo/upload-artifact.yml` parses via StepDefinition.model_validate with type=internal and command='artifacts.upload'."""
        template_path = Filesystem().catalog_templates_dir / "steps" / "dovo" / "upload-artifact.yml"
        parsed = yaml.safe_load(template_path.read_text(encoding="utf-8"))

        step = StepDefinition.model_validate(parsed)

        assert step.type == StepType.INTERNAL
        assert step.command == "artifacts.upload"


class DownloadArtifactCatalogStepValidationTests:
    """[tier-1/unit] Catalog validation: `steps/dovo/download-artifact.yml` parses as a type=internal StepDefinition."""

    def test_download_artifact_template_validates_as_step_definition(self) -> None:
        """[tier-1/unit] Catalog validation: `steps/dovo/download-artifact.yml` parses via StepDefinition.model_validate with type=internal and command='artifacts.download'."""
        template_path = Filesystem().catalog_templates_dir / "steps" / "dovo" / "download-artifact.yml"
        parsed = yaml.safe_load(template_path.read_text(encoding="utf-8"))

        step = StepDefinition.model_validate(parsed)

        assert step.type == StepType.INTERNAL
        assert step.command == "artifacts.download"


class SeededAgentTemplateToolsTests:
    @pytest.mark.parametrize(
        ("template", "expected_capabilities"),
        [
            pytest.param("ai-planner", {("read", None)}, id="planner-read-only"),
            pytest.param("ai-reviewer", {("read", None)}, id="reviewer-read-only"),
            pytest.param(
                "ai-code-patcher",
                {("read", None), ("write", None), ("read", "scratch"), ("write", "scratch")},
                id="patcher-read-write-both-roots",
            ),
        ],
    )
    def test_seeded_agent_template_declares_expected_explicit_policy(
        self, template: str, expected_capabilities: set[tuple[str, str | None]]
    ) -> None:
        """[tier-1/unit] StepDefinition.model_validate(<packaged template>): tools is an explicit ToolPolicy whose allow rules are exactly the expected (capability, root) pairs with no denies and allow_all False."""
        template_path = Filesystem().catalog_templates_dir / "steps" / "dovo" / f"{template}.yml"

        step = StepDefinition.model_validate(yaml.safe_load(template_path.read_text(encoding="utf-8")))

        assert step.tools is not None
        assert {(rule.capability.value, rule.root) for rule in step.tools.allow} == expected_capabilities
        assert step.tools.deny == []
        assert step.tools.allow_all is False
