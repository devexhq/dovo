"""Contract tests pinning the bundled `dovo/upload-artifact` and `dovo/download-artifact` catalog step templates."""

from __future__ import annotations

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
