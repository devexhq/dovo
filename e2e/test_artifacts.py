"""End-to-end tests for artifact archiving, transfers, cataloging, downloading, and pruning."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

from e2e.conftest import DovoRunner


def _write_blueprint(project: Path, name: str, content: str, *, runner: DovoRunner | None = None) -> Path:
    """Write a blueprint YAML definition into the project repository catalog and commit it."""
    blueprint_file = project / ".dovo" / "catalog" / "blueprints" / f"{name}.yml"
    blueprint_file.parent.mkdir(parents=True, exist_ok=True)
    blueprint_file.write_text(content, encoding="utf-8")
    if runner is not None:
        runner(["blueprint", "list"], cwd=project)
    subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", f"chore: add blueprint {name}"], cwd=project, check=True, capture_output=True
    )
    return blueprint_file


def _parse_json(stdout: str) -> dict[str, object]:
    """Extract and parse first JSON payload from command output."""
    for line in stdout.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("{") and trimmed.endswith("}"):
            try:
                data = json.loads(trimmed)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
    return json.loads(stdout)


@pytest.mark.e2e
@pytest.mark.fixture
class ArtifactsManagementCliTests:
    """E2E tests for artifact archiving, internal transfers, listing, downloading, and pruning."""

    def test_declarative_artifacts_archived_on_step_success(
        self,
        run_dovo: DovoRunner,
        initialized_project: Path,
    ) -> None:
        """Scenario: Declarative step artifacts archives matching files to persistent storage on step success.

        Given an initialized workspace with a blueprint defining declarative step artifacts
        When dovo run is executed
        Then matching files are archived upon step success and recorded in artifacts catalog
        """
        _write_blueprint(
            initialized_project,
            "decl-artifact-bp",
            """version: "1.0"
name: decl-artifact-bp
id: decl-artifact-bp
steps:
  - id: build-step
    run: mkdir -p dist && echo "console.log('bundle');" > dist/bundle.js && echo "docs" > dist/README.md
    artifacts:
      - name: build-dist
        path: "dist/*"
""",
            runner=run_dovo,
        )

        session_id = "sess-art-decl"
        run_result = run_dovo(["run", "decl-artifact-bp", "--session-id", session_id], cwd=initialized_project)
        assert run_result.exit_code == 0

        list_result = run_dovo(
            ["artifacts", "list", "--session", session_id, "--format", "json"],
            cwd=initialized_project,
        )
        assert list_result.exit_code == 0
        list_data = _parse_json(list_result.stdout)
        payload = list_data.get("payload", {})
        assert isinstance(payload, dict)
        artifacts = payload.get("artifacts", [])
        assert isinstance(artifacts, list)
        assert len(artifacts) == 1

        record = artifacts[0]
        assert record.get("name") == "build-dist"
        assert record.get("session_id") == session_id
        assert record.get("file_count") == 2

        dovo_home = Path(os.environ.get("DOVO_HOME", "/tmp/dovo-home"))
        matching_manifests = list(dovo_home.glob(f"**/artifacts/{session_id}/build-dist/manifest.json"))
        assert len(matching_manifests) == 1
        assert matching_manifests[0].is_file()

        manifest_data = json.loads(matching_manifests[0].read_text(encoding="utf-8"))
        assert manifest_data.get("name") == "build-dist"
        assert manifest_data.get("file_count") == 2
        files = manifest_data.get("files", [])
        assert len(files) == 2
        file_paths = {f.get("path") for f in files}
        assert "dist/bundle.js" in file_paths
        assert "dist/README.md" in file_paths

    def test_internal_commands_artifacts_transfer_within_session(
        self,
        run_dovo: DovoRunner,
        initialized_project: Path,
    ) -> None:
        """Scenario: Blueprint steps executing internal commands transfer artifact bundles within a session.

        Given a blueprint using artifacts.upload and artifacts.download internal commands
        When dovo run executes the blueprint
        Then the artifact bundle is uploaded by the producer step and extracted by the consumer step
        """
        _write_blueprint(
            initialized_project,
            "transfer-bp",
            """version: "1.0"
name: transfer-bp
id: transfer-bp
steps:
  - id: step-producer
    run: mkdir -p payload && echo "secret payload data" > payload/data.txt
  - id: step-upload
    type: internal
    command: artifacts.upload
    env:
      ARTIFACT_NAME: inter-step-bundle
      ARTIFACT_PATH: "payload/*"
  - id: step-download
    type: internal
    command: artifacts.download
    env:
      ARTIFACT_NAME: inter-step-bundle
      ARTIFACT_DEST: "restored/"
  - id: step-consumer
    run: grep -q "secret payload data" restored/payload/data.txt
""",
            runner=run_dovo,
        )

        session_id = "sess-art-transfer"
        run_result = run_dovo(["run", "transfer-bp", "--session-id", session_id], cwd=initialized_project)
        assert run_result.exit_code == 0

        list_result = run_dovo(
            ["artifacts", "list", "--session", session_id, "--format", "json"],
            cwd=initialized_project,
        )
        assert list_result.exit_code == 0
        list_data = _parse_json(list_result.stdout)
        payload = list_data.get("payload", {})
        assert isinstance(payload, dict)
        artifacts = payload.get("artifacts", [])
        assert isinstance(artifacts, list)
        assert len(artifacts) == 1
        assert artifacts[0].get("name") == "inter-step-bundle"

    def test_artifacts_list_displays_bundles_and_session_filter(
        self,
        run_dovo: DovoRunner,
        initialized_project: Path,
    ) -> None:
        """Scenario: dovo artifacts list displays published bundle names, file counts, and checksums with session filtering.

        Given multiple sessions that published artifact bundles
        When dovo artifacts list is invoked with and without the --session filter
        Then all bundles are listed globally and filtered strictly when a session is specified
        """
        _write_blueprint(
            initialized_project,
            "bundle-one-bp",
            """version: "1.0"
name: bundle-one-bp
id: bundle-one-bp
steps:
  - id: make-files-one
    run: mkdir -p b1 && echo "alpha" > b1/a.txt && echo "beta" > b1/b.txt
    artifacts:
      - name: bundle-one
        path: "b1/*"
""",
            runner=run_dovo,
        )
        _write_blueprint(
            initialized_project,
            "bundle-two-bp",
            """version: "1.0"
name: bundle-two-bp
id: bundle-two-bp
steps:
  - id: make-files-two
    run: mkdir -p b2 && echo "gamma" > b2/c.txt
    artifacts:
      - name: bundle-two
        path: "b2/*"
""",
            runner=run_dovo,
        )

        session_one = "sess-bundle-1"
        session_two = "sess-bundle-2"
        run_dovo(["run", "bundle-one-bp", "--session-id", session_one], cwd=initialized_project)
        run_dovo(["run", "bundle-two-bp", "--session-id", session_two], cwd=initialized_project)

        global_list = run_dovo(["artifacts", "list", "--format", "json"], cwd=initialized_project)
        assert global_list.exit_code == 0
        global_data = _parse_json(global_list.stdout)
        global_payload = global_data.get("payload", {})
        assert isinstance(global_payload, dict)
        all_artifacts = global_payload.get("artifacts", [])
        assert isinstance(all_artifacts, list)
        all_names = {a.get("name") for a in all_artifacts}
        assert "bundle-one" in all_names
        assert "bundle-two" in all_names

        filtered_list = run_dovo(
            ["artifacts", "list", "--session", session_one, "--format", "json"],
            cwd=initialized_project,
        )
        assert filtered_list.exit_code == 0
        filtered_data = _parse_json(filtered_list.stdout)
        filtered_payload = filtered_data.get("payload", {})
        assert isinstance(filtered_payload, dict)
        filtered_artifacts = filtered_payload.get("artifacts", [])
        assert isinstance(filtered_artifacts, list)
        assert len(filtered_artifacts) == 1
        assert filtered_artifacts[0].get("name") == "bundle-one"
        assert filtered_artifacts[0].get("file_count") == 2

        term_list = run_dovo(["artifacts", "list"], cwd=initialized_project)
        assert term_list.exit_code == 0
        assert "bundle-one" in term_list.stdout
        assert "bundle-two" in term_list.stdout

    def test_artifacts_download_extracts_and_verifies_bundle(
        self,
        run_dovo: DovoRunner,
        initialized_project: Path,
        clean_e2e_env: Path,
    ) -> None:
        """Scenario: dovo artifacts download extracts and verifies bundle integrity in the target directory.

        Given a published artifact bundle from an executed session
        When dovo artifacts download is run specifying the session ID, artifact name, and target destination
        Then the artifact files are verified against the manifest checksums and extracted to the destination
        """
        _write_blueprint(
            initialized_project,
            "download-bp",
            """version: "1.0"
name: download-bp
id: download-bp
steps:
  - id: step-generate
    run: mkdir -p output && echo "integrity-verified-payload" > output/target.txt
    artifacts:
      - name: verified-pkg
        path: "output/*"
""",
            runner=run_dovo,
        )

        session_id = "sess-download-check"
        run_dovo(["run", "download-bp", "--session-id", session_id], cwd=initialized_project)

        dest_dir = clean_e2e_env / "download_extracted"
        download_res = run_dovo(
            ["artifacts", "download", session_id, "verified-pkg", "--dest", str(dest_dir)],
            cwd=initialized_project,
        )
        assert download_res.exit_code == 0

        extracted_file = dest_dir / "output" / "target.txt"
        assert extracted_file.is_file()
        assert extracted_file.read_text(encoding="utf-8").strip() == "integrity-verified-payload"

    def test_artifacts_prune_dry_run_and_force(
        self,
        run_dovo: DovoRunner,
        initialized_project: Path,
    ) -> None:
        """Scenario: dovo artifacts prune detects expired bundles under dry-run and removes them with --force.

        Given a published artifact bundle with an expiration timestamp in the past
        When dovo artifacts prune is executed with --dry-run and then with --force
        Then --dry-run detects the expired bundle without deleting it, and --force deletes it from disk and database
        """
        _write_blueprint(
            initialized_project,
            "prune-bp",
            """version: "1.0"
name: prune-bp
id: prune-bp
steps:
  - id: step-make-artifact
    run: mkdir -p out && echo "ephemeral data" > out/data.txt
    artifacts:
      - name: prune-target-bundle
        path: "out/*"
        retention_days: 10
""",
            runner=run_dovo,
        )

        session_id = "sess-prune-test"
        run_res = run_dovo(["run", "prune-bp", "--session-id", session_id], cwd=initialized_project)
        assert run_res.exit_code == 0

        dovo_home = Path(os.environ.get("DOVO_HOME", "/tmp/dovo-home"))
        db_file = dovo_home / "data" / "dovo.db"
        assert db_file.is_file()

        connection = sqlite3.connect(db_file)
        connection.execute("UPDATE artifacts SET expires_at = '2020-01-01 00:00:00' WHERE name = 'prune-target-bundle'")
        connection.commit()
        connection.close()

        dry_res = run_dovo(["artifacts", "prune", "--dry-run", "--force", "--format", "json"], cwd=initialized_project)
        assert dry_res.exit_code == 0
        dry_data = _parse_json(dry_res.stdout)
        dry_payload = dry_data.get("payload", {})
        assert isinstance(dry_payload, dict)
        assert dry_payload.get("dry_run") is True
        items = dry_payload.get("items", [])
        assert isinstance(items, list)
        assert len(items) == 1
        assert items[0].get("name") == "prune-target-bundle"

        list_after_dry = run_dovo(["artifacts", "list", "--format", "json"], cwd=initialized_project)
        assert list_after_dry.exit_code == 0
        after_dry_data = _parse_json(list_after_dry.stdout)
        after_dry_payload = after_dry_data.get("payload", {})
        assert isinstance(after_dry_payload, dict)
        assert len(after_dry_payload.get("artifacts", [])) == 1

        force_res = run_dovo(["artifacts", "prune", "--force", "--format", "json"], cwd=initialized_project)
        assert force_res.exit_code == 0
        force_data = _parse_json(force_res.stdout)
        force_payload = force_data.get("payload", {})
        assert isinstance(force_payload, dict)
        assert force_payload.get("dry_run") is False
        pruned_items = force_payload.get("items", [])
        assert isinstance(pruned_items, list)
        assert len(pruned_items) == 1
        assert pruned_items[0].get("name") == "prune-target-bundle"
        assert pruned_items[0].get("pruned") is True

        list_after_force = run_dovo(["artifacts", "list", "--format", "json"], cwd=initialized_project)
        assert list_after_force.exit_code == 0
        after_force_data = _parse_json(list_after_force.stdout)
        after_force_payload = after_force_data.get("payload", {})
        assert isinstance(after_force_payload, dict)
        assert len(after_force_payload.get("artifacts", [])) == 0

        matching_dirs = list(dovo_home.glob(f"**/artifacts/{session_id}/prune-target-bundle"))
        assert len(matching_dirs) == 0
