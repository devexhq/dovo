"""Tier 1 contract: .dovo/catalog/blueprints/ship.yml and .dovo/catalog/steps/ship/*.yml resolve and drive stage transitions."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.common.models import FailurePolicy
from dovo.core.catalog import Catalog
from dovo.core.catalog.definitions import BlueprintDefinition, LoopStepBlock, StepDefinition, StepType
from dovo.core.catalog.models import CatalogItemType, CatalogTier
from dovo.core.catalog.services.inventory import scan_and_index_tier
from dovo.core.catalog.services.resolve_step import resolve_step_definition
from dovo.core.inputs import resolve_inputs
from dovo.engine.executors.conditions import evaluate_condition
from dovo.engine.executors.models import StepResult
from tests.harness.workspace_paths import initialized_workspace_paths
from tests.lint.astlib import REPO_ROOT

HEAD_SHA = "a" * 40
NEW_HEAD_SHA = "b" * 40
PR_HEAD_SHA = "c" * 40
ISSUE_BODY = "## Why\nGrounded ticket body.\n"
ISSUE_BODY_SHA = hashlib.sha256(ISSUE_BODY.encode("utf-8")).hexdigest()

EXPECTED_STEP_KEYS: frozenset[str] = frozenset(
    {
        "ship/prepare",
        "ship/next",
        "ship/assess",
        "ship/investigate",
        "ship/plan",
        "ship/implement",
        "ship/validate",
        "ship/review",
        "ship/record",
        "ship/publication-gate",
        "ship/publish",
        "ship/report",
    }
)
AGENT_STAGE_PROMPTS: dict[str, str] = {
    "assess": ".dovo/catalog/prompts/sdlc/ship/ticket-route.md",
    "investigate": ".dovo/catalog/prompts/sdlc/ship/ticket-investigate.md",
    "plan": ".dovo/catalog/prompts/sdlc/ship/ticket-plan.md",
    "implement": ".dovo/catalog/prompts/sdlc/ship/ticket-implement.md",
    "review": ".dovo/catalog/prompts/sdlc/ship/ticket-review.md",
}


def _load_ship_state() -> ModuleType:
    """Load .dovo/catalog/scripts/sdlc/ship/ship_state.py by file path."""
    script_path = REPO_ROOT / ".dovo/catalog/scripts/sdlc/ship/ship_state.py"
    spec = importlib.util.spec_from_file_location("sdlc_ship_state_blueprint", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["sdlc_ship_state_blueprint"] = module
    spec.loader.exec_module(module)
    return module


SHIP = _load_ship_state()


def _write_json(path: Path, payload: object) -> None:
    """Write formatted JSON to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _completed(
    args: Sequence[str], *, stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    """Build a CompletedProcess for fake subprocess.run responses."""
    return subprocess.CompletedProcess(args=list(args), returncode=returncode, stdout=stdout, stderr=stderr)


def _mock_git_and_gh(monkeypatch: pytest.MonkeyPatch, head_sha_ref: list[str]) -> None:
    """Mock git rev-parse HEAD and gh api issue lookup boundary calls."""

    def fake_run(
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        check: bool = True,
        capture_output: bool = True,
        text: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, check, capture_output, text
        cmd = tuple(args)
        if cmd[:3] == ("git", "rev-parse", "HEAD"):
            return _completed(cmd, stdout=f"{head_sha_ref[0]}\n")
        if cmd[:3] == ("gh", "api", "repos/{owner}/{repo}/issues/1373"):
            return _completed(
                cmd,
                stdout=json.dumps(
                    {
                        "number": 1373,
                        "title": "Wire the ship blueprint",
                        "body": ISSUE_BODY,
                        "html_url": "https://github.com/devexhq/dovo/issues/1373",
                    }
                ),
            )
        raise AssertionError(f"Unexpected subprocess command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)


def _setup_workspace_env(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Prepare isolated workspace directory and clean environment."""
    monkeypatch.chdir(workspace)
    for var in (
        "DOVO_OUTPUT",
        "SHIP_ISSUE",
        "SHIP_PULL_REQUEST",
        "SHIP_ACTION",
        "SHIP_QUESTION_ID",
        "SHIP_ANSWER",
    ):
        monkeypatch.delenv(var, raising=False)


def _run_ship(args: list[str], capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    """Run SHIP.main(args) and parse JSON emitted to stdout."""
    capsys.readouterr()
    exit_code = SHIP.main(args)
    assert exit_code == 0
    raw = capsys.readouterr().out.strip()
    return json.loads(raw) if raw else {}


def _run_record(
    capsys: pytest.CaptureFixture[str],
    *,
    action: str,
    question_id: str = "",
    answer: str = "",
) -> dict[str, object]:
    """Invoke `ship_state.py record` via CLI flags."""
    args = ["record", "--action", action]
    if question_id:
        args.extend(["--question-id", question_id])
    if answer:
        args.extend(["--answer", answer])
    return _run_ship(args, capsys)


def _seed_ship_catalog(workspace: Path) -> WorkspacePaths:
    """Copy ship.yml and ship/*.yml into an isolated workspace and index the REPO tier."""
    paths = initialized_workspace_paths(workspace)
    blueprints_dir = workspace / ".dovo" / "catalog" / "blueprints"
    steps_dir = workspace / ".dovo" / "catalog" / "steps" / "ship"
    blueprints_dir.mkdir(parents=True, exist_ok=True)
    steps_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / ".dovo/catalog/blueprints/ship.yml", blueprints_dir / "ship.yml")
    for step_file in sorted((REPO_ROOT / ".dovo/catalog/steps/ship").glob("*.yml")):
        shutil.copy2(step_file, steps_dir / step_file.name)
    return paths


def _load_ship_blueprint() -> BlueprintDefinition:
    """Load and validate .dovo/catalog/blueprints/ship.yml as a BlueprintDefinition."""
    raw = Catalog.read_yaml(REPO_ROOT / ".dovo/catalog/blueprints/ship.yml")
    return BlueprintDefinition.from_document(raw, key="ship")


def _delivery_loop(blueprint: BlueprintDefinition) -> LoopStepBlock:
    """Return the delivery LoopStepBlock from ship.yml."""
    loop = blueprint.steps[1]
    assert isinstance(loop, LoopStepBlock)
    return loop


def _step_result(step_id: str, outputs: dict[str, object]) -> StepResult:
    """Build a succeeded StepResult with stringified output values."""
    str_outputs = {key: str(value) for key, value in outputs.items()}
    return StepResult(
        step_id=step_id,
        status="completed",
        exit_code=0,
        stdout=json.dumps(outputs),
        stderr="",
        duration_seconds=0.1,
        outputs=str_outputs,
    )


def _active_do_steps(loop: LoopStepBlock, step_results: dict[str, StepResult]) -> list[str]:
    """Return the IDs of loop.do steps whose when guard passes (or has no when guard) for step_results."""
    active: list[str] = []
    for step in loop.do:
        if step.when is None:
            active.append(step.id)
            continue
        if all(evaluate_condition(expr, step_results=step_results).passed for expr in step.when):
            active.append(step.id)
    return active


def _until_passed(loop: LoopStepBlock, step_results: dict[str, StepResult]) -> bool:
    """Evaluate the delivery loop's until condition list against step_results."""
    return all(evaluate_condition(expr, step_results=step_results).passed for expr in loop.until)


def _seed_contract_and_progress(
    workspace: Path,
    *,
    head_sha: str = HEAD_SHA,
    pull_request: dict[str, object] | None = None,
) -> None:
    """Write initial contract.json and progress.json in workspace."""
    ship_dir = workspace / ".agentic" / "ship"
    ship_dir.mkdir(parents=True, exist_ok=True)
    contract = {
        "schema_version": 1,
        "issue": {
            "number": 1373,
            "title": "Wire the ship blueprint",
            "body": ISSUE_BODY,
            "body_sha": ISSUE_BODY_SHA,
            "url": "https://github.com/devexhq/dovo/issues/1373",
        },
        "pull_request": pull_request,
        "base_sha": HEAD_SHA,
        "head_sha": head_sha,
    }
    progress = SHIP.initial_progress(1373, ISSUE_BODY_SHA, head_sha)
    _write_json(ship_dir / "contract.json", contract)
    _write_json(ship_dir / "progress.json", progress)


def _write_stage_result(
    workspace: Path,
    *,
    stage: str,
    disposition: str,
    head_sha: str = HEAD_SHA,
    extra: Mapping[str, object] | None = None,
) -> None:
    """Write a stage report markdown file and .agentic/ship/stage.json."""
    ship_dir = workspace / ".agentic" / "ship"
    report_rel = ".agentic/review.md" if stage == "review" else f".agentic/ship/{stage}.md"
    report_path = workspace / report_rel
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(f"# {stage} ({disposition})\n", encoding="utf-8")
    payload: dict[str, object] = {
        "schema_version": 1,
        "stage": stage,
        "disposition": disposition,
        "summary": f"{stage} -> {disposition}",
        "body_sha": ISSUE_BODY_SHA,
        "head_sha": head_sha,
        "report_path": report_rel,
    }
    if extra:
        payload.update(extra)
    _write_json(ship_dir / "stage.json", payload)


def _write_ready_review_json(workspace: Path, *, head_sha: str) -> None:
    """Write a clean .agentic/review.json matching head_sha and ISSUE_BODY_SHA."""
    _write_json(
        workspace / ".agentic" / "review.json",
        {
            "issue": 1373,
            "verdict": "ready",
            "body_sha": ISSUE_BODY_SHA,
            "head_sha": head_sha,
            "findings": [],
        },
    )


class ShipBlueprintCatalogResolutionTests:
    """Verify Catalog indexing, validation, and uses: ship/* step resolution for ship.yml."""

    def test_catalog_indexes_ship_blueprint_and_all_twelve_steps(self, isolated_workspace: Path) -> None:
        paths = _seed_ship_catalog(isolated_workspace)
        scan = scan_and_index_tier(CatalogTier.REPO, paths)

        assert scan.skipped_files == ()
        blueprint_keys = {rec.key for rec in scan.records if rec.item_type == CatalogItemType.BLUEPRINT}
        step_keys = {rec.key for rec in scan.records if rec.item_type == CatalogItemType.STEP}
        assert "ship" in blueprint_keys
        assert step_keys == EXPECTED_STEP_KEYS

        catalog = Catalog(paths)
        bp_result = catalog.validate(
            "ship",
            item_type=CatalogItemType.BLUEPRINT,
            blueprint_cls=BlueprintDefinition,
            step_cls=StepDefinition,
        )
        assert (bp_result.valid, bp_result.errors, bp_result.warnings) == (True, [], [])

        for step_key in sorted(EXPECTED_STEP_KEYS):
            step_result = catalog.validate(
                step_key,
                item_type=CatalogItemType.STEP,
                blueprint_cls=BlueprintDefinition,
                step_cls=StepDefinition,
            )
            assert (step_result.valid, step_result.errors, step_result.warnings) == (True, [], [])

    def test_ship_blueprint_shape_and_delivery_loop_contract(self) -> None:
        blueprint = _load_ship_blueprint()

        assert blueprint.name == "ship"
        assert blueprint.version == 1
        assert blueprint.use_worktree is True
        assert set(blueprint.inputs) == {"issue", "pull_request"}
        assert blueprint.inputs["issue"].required is True
        assert blueprint.inputs["issue"].aliases == ["-i", "--issue"]
        assert blueprint.inputs["pull_request"].default == ""
        assert blueprint.inputs["pull_request"].aliases == ["-p", "--pull-request"]
        assert blueprint.defaults.on_failure is not None
        assert blueprint.defaults.on_failure.action == FailurePolicy.ABORT

        top_ids = [step.id for step in blueprint.steps]
        assert top_ids == ["prepare", "delivery", "publication-gate", "publish", "report"]

        loop = _delivery_loop(blueprint)
        assert loop.max_iterations == 24
        assert loop.on_max_iterations == FailurePolicy.CONTINUE
        assert loop.until == ['steps.record.outputs.outcome in ["ready", "no_action", "blocked", "incomplete"]']
        assert [step.id for step in loop.do] == [
            "next",
            "assess",
            "investigate",
            "plan",
            "implement",
            "validate",
            "review",
            "human",
            "record",
        ]

        human_step = loop.do[7]
        assert human_step.type == StepType.ASK
        assert human_step.when == ['steps.next.outputs.action == "ask"']
        assert human_step.interaction is not None
        assert human_step.interaction.mode == "text"
        assert human_step.interaction.artifact == "ship-decision"
        assert human_step.interaction.message == "{{ steps.next.outputs.question }}"

    @pytest.mark.parametrize("flag", ["-p", "--pull-request"])
    def test_pull_request_flag_binds_the_pull_request_input(self, flag: str) -> None:
        result = resolve_inputs(_load_ship_blueprint().inputs, cli_args=["-i", "1394", flag, "1399"])

        assert result.values == {"issue": "1394", "pull_request": "1399"}
        assert (result.missing, result.errors, result.warnings) == ([], [], [])

    def test_every_uses_step_resolves_preserving_when_env_and_artifacts(self, isolated_workspace: Path) -> None:
        paths = _seed_ship_catalog(isolated_workspace)
        scan_and_index_tier(CatalogTier.REPO, paths)
        catalog = Catalog(paths)
        blueprint = _load_ship_blueprint()
        loop = _delivery_loop(blueprint)

        all_steps = [
            blueprint.steps[0],
            *loop.do,
            blueprint.steps[2],
            blueprint.steps[3],
            blueprint.steps[4],
        ]
        resolved_by_id = {
            step.id: resolve_step_definition(step, catalog=catalog)
            for step in all_steps
            if isinstance(step, StepDefinition)
        }

        for stage_id, prompt_path in AGENT_STAGE_PROMPTS.items():
            resolved = resolved_by_id[stage_id]
            assert resolved.type == StepType.AGENT
            assert resolved.when == [f'steps.next.outputs.action == "{stage_id}"']
            assert resolved.tools is not None and resolved.tools.allow_all is True
            assert resolved.on_failure.action == FailurePolicy.ABORT
            assert resolved.prompt is not None
            assert prompt_path in resolved.prompt
            assert ".agentic/ship/stage.json" in resolved.prompt

        assert resolved_by_id["prepare"].type == StepType.COMMAND
        assert resolved_by_id["prepare"].env == {
            "SHIP_ISSUE": "${{ inputs.issue }}",
            "SHIP_PULL_REQUEST": "${{ inputs.pull_request }}",
        }
        assert [a.name for a in resolved_by_id["prepare"].artifacts] == ["ship-contract"]
        assert [a.name for a in resolved_by_id["next"].artifacts] == ["ship-decision"]
        assert resolved_by_id["record"].env == {
            "SHIP_ACTION": "{{ steps.next.outputs.action }}",
            "SHIP_QUESTION_ID": "{{ steps.next.outputs.question_id || '' }}",
            "SHIP_ANSWER": "{{ steps.human.outputs.text || '' }}",
        }
        assert resolved_by_id["publish"].when == ['steps.publication-gate.outputs.outcome == "ready"']
        assert [a.name for a in resolved_by_id["report"].artifacts] == ["ship-result", "ship-report"]


class ShipBlueprintTransitionTests:
    """Verify end-to-end stage transitions and condition guards across ship.yml."""

    def test_clear_ticket_transition_reaches_ready_and_enables_publish(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        blueprint = _load_ship_blueprint()
        loop = _delivery_loop(blueprint)
        publish_step = blueprint.steps[3]
        assert isinstance(publish_step, StepDefinition) and publish_step.when is not None

        _setup_workspace_env(isolated_workspace, monkeypatch)
        head_sha_ref = [HEAD_SHA]
        _mock_git_and_gh(monkeypatch, head_sha_ref)
        _seed_contract_and_progress(isolated_workspace)

        step_results: dict[str, StepResult] = {}
        stages: list[tuple[str, str, str, Mapping[str, object] | None]] = [
            ("assess", "implement", HEAD_SHA, None),
            ("implement", "validate", NEW_HEAD_SHA, None),
            ("validate", "passed", NEW_HEAD_SHA, {"commands": ["pytest"]}),
            ("review", "ready", NEW_HEAD_SHA, None),
        ]

        for expected_action, disposition, stage_head, extra in stages:
            next_out = _run_ship(["next"], capsys)
            assert next_out["action"] == expected_action
            step_results["next"] = _step_result("next", next_out)
            assert _active_do_steps(loop, step_results) == ["next", expected_action, "record"]

            head_sha_ref[0] = stage_head
            _write_stage_result(
                isolated_workspace, stage=expected_action, disposition=disposition, head_sha=stage_head, extra=extra
            )
            if expected_action == "review":
                _write_ready_review_json(isolated_workspace, head_sha=stage_head)

            rec_out = _run_record(capsys, action=expected_action)
            step_results["record"] = _step_result("record", rec_out)
            assert _until_passed(loop, step_results) is (expected_action == "review")

        gate_out = _run_ship(["publication-gate"], capsys)
        assert gate_out == {"outcome": "ready"}
        step_results["publication-gate"] = _step_result("publication-gate", gate_out)
        assert all(evaluate_condition(expr, step_results=step_results).passed for expr in publish_step.when)

        _write_json(
            isolated_workspace / ".agentic/ship/pr.json",
            {"number": 99, "url": "https://github.com/devexhq/dovo/pull/99", "head_sha": NEW_HEAD_SHA},
        )
        report_out = _run_ship(["report"], capsys)
        assert report_out == {
            "status": "published",
            "outcome": "ready",
            "pr_url": "https://github.com/devexhq/dovo/pull/99",
        }

    @pytest.mark.parametrize("middle_stage", ["investigate", "plan"])
    def test_investigate_and_plan_routes_dispatch_matching_stage_steps(
        self,
        isolated_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        middle_stage: str,
    ) -> None:
        loop = _delivery_loop(_load_ship_blueprint())
        _setup_workspace_env(isolated_workspace, monkeypatch)
        head_sha_ref = [HEAD_SHA]
        _mock_git_and_gh(monkeypatch, head_sha_ref)
        _seed_contract_and_progress(isolated_workspace)

        step_results: dict[str, StepResult] = {}
        next_1 = _run_ship(["next"], capsys)
        step_results["next"] = _step_result("next", next_1)
        assert _active_do_steps(loop, step_results) == ["next", "assess", "record"]

        _write_stage_result(isolated_workspace, stage="assess", disposition=middle_stage)
        rec_1 = _run_record(capsys, action="assess")
        step_results["record"] = _step_result("record", rec_1)
        assert not _until_passed(loop, step_results)

        next_2 = _run_ship(["next"], capsys)
        assert next_2["action"] == middle_stage
        step_results["next"] = _step_result("next", next_2)
        assert _active_do_steps(loop, step_results) == ["next", middle_stage, "record"]

        _write_stage_result(isolated_workspace, stage=middle_stage, disposition="implement")
        rec_2 = _run_record(capsys, action=middle_stage)
        assert rec_2 == {"outcome": "working", "next_action": "implement"}

    def test_existing_pr_continuation_assesses_then_reviews_then_validates(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        loop = _delivery_loop(_load_ship_blueprint())
        _setup_workspace_env(isolated_workspace, monkeypatch)
        head_sha_ref = [PR_HEAD_SHA]
        _mock_git_and_gh(monkeypatch, head_sha_ref)
        _seed_contract_and_progress(
            isolated_workspace,
            head_sha=PR_HEAD_SHA,
            pull_request={
                "number": 42,
                "url": "https://github.com/devexhq/dovo/pull/42",
                "head_ref": "luckner/1373-ship",
                "head_sha": PR_HEAD_SHA,
                "base_ref": "main",
            },
        )

        _write_stage_result(isolated_workspace, stage="assess", disposition="review", head_sha=PR_HEAD_SHA)
        _run_record(capsys, action="assess")

        next_review = _run_ship(["next"], capsys)
        assert next_review["action"] == "review"
        _write_stage_result(
            isolated_workspace,
            stage="review",
            disposition="changes_requested",
            head_sha=PR_HEAD_SHA,
            extra={"findings": [{"id": "F-1", "summary": "Missing edge test"}]},
        )
        rec_review = _run_record(capsys, action="review")
        assert rec_review == {"outcome": "working", "next_action": "implement"}

        head_sha_ref[0] = NEW_HEAD_SHA
        _write_stage_result(isolated_workspace, stage="implement", disposition="validate", head_sha=NEW_HEAD_SHA)
        _run_record(capsys, action="implement")

        _write_stage_result(isolated_workspace, stage="validate", disposition="passed", head_sha=NEW_HEAD_SHA)
        _run_record(capsys, action="validate")

        _write_stage_result(isolated_workspace, stage="review", disposition="ready", head_sha=NEW_HEAD_SHA)
        _write_ready_review_json(isolated_workspace, head_sha=NEW_HEAD_SHA)
        rec_final = _run_record(capsys, action="review")
        assert _until_passed(loop, {"record": _step_result("record", rec_final)}) is True

    def test_serial_multi_question_ask_resumes_stage_and_ignores_stale_human_output(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        loop = _delivery_loop(_load_ship_blueprint())
        _setup_workspace_env(isolated_workspace, monkeypatch)
        head_sha_ref = [HEAD_SHA]
        _mock_git_and_gh(monkeypatch, head_sha_ref)
        _seed_contract_and_progress(isolated_workspace)

        questions = [
            {"id": "Q-1", "question": "Which config tier should own the default?"},
            {"id": "Q-2", "question": "Should strict mode reject unknown keys?"},
        ]
        _write_stage_result(
            isolated_workspace,
            stage="assess",
            disposition="ask",
            extra={"questions": questions},
        )
        rec_ask = _run_record(capsys, action="assess")
        assert rec_ask == {"outcome": "working", "next_action": "ask"}

        step_results: dict[str, StepResult] = {}
        for q_id, answer_text, expected_next in [
            ("Q-1", "Repository tier", "ask"),
            ("Q-2", "Yes, reject unknown keys", "assess"),
        ]:
            next_q = _run_ship(["next"], capsys)
            assert (next_q["action"], next_q["question_id"]) == ("ask", q_id)
            step_results["next"] = _step_result("next", next_q)
            assert _active_do_steps(loop, step_results) == ["next", "human", "record"]

            step_results["human"] = _step_result("human", {"text": answer_text})
            rec_q = _run_record(
                capsys,
                action=str(next_q["action"]),
                question_id=str(next_q["question_id"]),
                answer=step_results["human"].outputs["text"],
            )
            assert rec_q == {"outcome": "working", "next_action": expected_next}

        next_resume = _run_ship(["next"], capsys)
        assert next_resume == {"action": "assess", "question_id": "", "question": ""}
        step_results["next"] = _step_result("next", next_resume)
        assert _active_do_steps(loop, step_results) == ["next", "assess", "record"]

        # step_results["human"] still carries the previous iteration's answer text;
        # passing it via SHIP_ANSWER on a non-ask iteration is ignored by record.
        _write_stage_result(isolated_workspace, stage="assess", disposition="implement")
        rec_resume = _run_record(
            capsys,
            action="assess",
            question_id="",
            answer=step_results["human"].outputs["text"],
        )
        assert rec_resume == {"outcome": "working", "next_action": "implement"}

    def test_validation_and_review_repair_loops_and_three_round_review_cap(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        blueprint = _load_ship_blueprint()
        loop = _delivery_loop(blueprint)
        publish_step = blueprint.steps[3]
        assert isinstance(publish_step, StepDefinition) and publish_step.when is not None

        _setup_workspace_env(isolated_workspace, monkeypatch)
        head_sha_ref = [NEW_HEAD_SHA]
        _mock_git_and_gh(monkeypatch, head_sha_ref)
        _seed_contract_and_progress(isolated_workspace, head_sha=NEW_HEAD_SHA)

        _write_stage_result(isolated_workspace, stage="assess", disposition="implement", head_sha=NEW_HEAD_SHA)
        _run_record(capsys, action="assess")
        _write_stage_result(isolated_workspace, stage="implement", disposition="validate", head_sha=NEW_HEAD_SHA)
        _run_record(capsys, action="implement")

        _write_stage_result(
            isolated_workspace,
            stage="validate",
            disposition="checks_failed",
            head_sha=NEW_HEAD_SHA,
            extra={"failed_checks": ["ruff check ."]},
        )
        rec_failed = _run_record(capsys, action="validate")
        assert rec_failed == {"outcome": "working", "next_action": "implement"}

        for round_num in range(1, 4):
            _write_stage_result(isolated_workspace, stage="implement", disposition="validate", head_sha=NEW_HEAD_SHA)
            _run_record(capsys, action="implement")
            _write_stage_result(isolated_workspace, stage="validate", disposition="passed", head_sha=NEW_HEAD_SHA)
            _run_record(capsys, action="validate")
            _write_stage_result(
                isolated_workspace,
                stage="review",
                disposition="changes_requested",
                head_sha=NEW_HEAD_SHA,
                extra={"findings": [{"id": f"F-{round_num}", "summary": "Still broken"}]},
            )
            rec_rev = _run_record(capsys, action="review")

        assert rec_rev == {"outcome": "incomplete", "next_action": ""}
        assert _until_passed(loop, {"record": _step_result("record", rec_rev)}) is True

        gate_out = _run_ship(["publication-gate"], capsys)
        assert gate_out["outcome"] == "incomplete"
        step_results = {"publication-gate": _step_result("publication-gate", gate_out)}
        assert not all(evaluate_condition(expr, step_results=step_results).passed for expr in publish_step.when)

    @pytest.mark.parametrize(
        ("disposition", "expected_outcome"),
        [
            ("no_action", "no_action"),
            ("blocked", "blocked"),
            ("implement", "incomplete"),
        ],
    )
    def test_terminal_no_action_blocked_and_loop_ceiling_skip_publish(
        self,
        isolated_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        disposition: str,
        expected_outcome: str,
    ) -> None:
        blueprint = _load_ship_blueprint()
        loop = _delivery_loop(blueprint)
        publish_step = blueprint.steps[3]
        assert isinstance(publish_step, StepDefinition) and publish_step.when is not None

        _setup_workspace_env(isolated_workspace, monkeypatch)
        head_sha_ref = [HEAD_SHA]
        _mock_git_and_gh(monkeypatch, head_sha_ref)
        _seed_contract_and_progress(isolated_workspace)

        _write_stage_result(isolated_workspace, stage="assess", disposition=disposition)
        rec_out = _run_record(capsys, action="assess")
        assert _until_passed(loop, {"record": _step_result("record", rec_out)}) is (disposition != "implement")

        gate_out = _run_ship(["publication-gate"], capsys)
        assert gate_out["outcome"] == expected_outcome
        step_results = {"publication-gate": _step_result("publication-gate", gate_out)}
        assert not all(evaluate_condition(expr, step_results=step_results).passed for expr in publish_step.when)

        report_out = _run_ship(["report"], capsys)
        assert report_out["outcome"] == expected_outcome
        assert report_out["pr_url"] == ""
