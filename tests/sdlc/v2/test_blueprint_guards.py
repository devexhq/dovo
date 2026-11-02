"""Tier 1 contract: the `when` guards, `if` blocks and loop `until` lists of the blueprints sdlc v2 runs decide which steps a run reaches.

Each blueprint is loaded from the catalog and every guard is evaluated by the engine's guard
functions, against the step outputs and the artifacts a run would have recorded by then.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Final

import pytest
import yaml

from dovo.core.artifacts.store import ArtifactStore
from dovo.core.catalog.definitions.blueprint import BlueprintDefinition
from dovo.core.catalog.definitions.step import IfStepBlock, LoopStepBlock, StepDefinition
from dovo.engine.executors.models import StepResult
from dovo.engine.guards import choose_branch, evaluate_guard, guard_skip
from dovo.engine.models import IfBranch
from tests.conftest import PublishArtifact
from tests.lint.astlib import REPO_ROOT

BLUEPRINTS_DIR: Final = REPO_ROOT / ".dovo" / "catalog" / "blueprints"

Entry = StepDefinition | LoopStepBlock | IfStepBlock


def _load(name: str) -> BlueprintDefinition:
    """Return the catalog blueprint name, validated as `dovo run` loads it."""
    raw = yaml.safe_load((BLUEPRINTS_DIR / f"{name}.yml").read_text(encoding="utf-8"))
    return BlueprintDefinition.from_document(raw, key=name)


def _walk(entries: list[Entry], parent: Entry | None = None) -> Iterator[tuple[Entry, Entry | None]]:
    """Yield every step, loop and `if` block of entries with the block holding it, or None at the top level."""
    for entry in entries:
        yield entry, parent
        if isinstance(entry, LoopStepBlock):
            yield from _walk(list(entry.do), entry)
        elif isinstance(entry, IfStepBlock):
            yield from _walk([*entry.then, *(entry.else_ or [])], entry)


class _Run:
    """The outputs a run recorded so far, and whether each guard of the blueprint lets its step run."""

    def __init__(self, blueprint: BlueprintDefinition, store: ArtifactStore) -> None:
        self._entries = {entry.id: (entry, parent) for entry, parent in _walk(list(blueprint.steps))}
        self._store = store
        self._results: dict[str, StepResult] = {}

    def record(self, step_id: str, **outputs: str) -> None:
        """Record step_id as completed with outputs."""
        self._results[step_id] = StepResult(
            step_id=step_id,
            status="completed",
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
            outputs=outputs,
        )

    def _passes(self, expressions: list[str]) -> bool:
        """Return whether every expression passes against the recorded outputs and the published artifacts."""
        return guard_skip(evaluate_guard(expressions, self._results, artifact_store=self._store)) is None

    def reaches(self, step_id: str) -> bool:
        """Return whether step_id runs: its enclosing loop starts or its `if` branch is chosen, and its `when` passes."""
        entry, parent = self._entries[step_id]
        if isinstance(parent, IfStepBlock):
            results = evaluate_guard(parent.condition, self._results, artifact_store=self._store)
            branch = choose_branch(results, has_else=parent.else_ is not None)
            if branch is not (IfBranch.THEN if entry in parent.then else IfBranch.ELSE):
                return False
        elif parent is not None and not self.reaches(parent.id):
            return False
        if isinstance(entry, IfStepBlock):
            return True
        return entry.when is None or self._passes(entry.when)

    def ends(self, loop_id: str) -> bool:
        """Return whether loop_id's `until` list ends the loop after the iteration recorded."""
        loop, _ = self._entries[loop_id]
        assert isinstance(loop, LoopStepBlock)
        return self._passes(loop.until)

    def agent_steps(self) -> set[str]:
        """Return the ids of the agent steps the run reaches."""
        return {
            step_id
            for step_id, (entry, _) in self._entries.items()
            if isinstance(entry, StepDefinition) and entry.type == "agent" and self.reaches(step_id)
        }


def _publish_json(publish_artifact: PublishArtifact, name: str, content: object) -> None:
    """Publish content as the one JSON file of artifact name."""
    publish_artifact(name, {f"{name}.json": json.dumps(content)})


def _plan_questions(open_questions: int) -> dict[str, object]:
    """Return a plan.json holding open_questions open questions."""
    return {"open_questions": [{"id": f"OQ-{n}"} for n in range(1, open_questions + 1)], "slices": []}


class IssuePlanGuardTests:
    @pytest.fixture
    def run(self, artifact_store: ArtifactStore) -> _Run:
        """Return a run of issue-plan-v2 with nothing recorded yet."""
        return _Run(_load("issue-plan-v2"), artifact_store)

    @pytest.fixture
    def questions_and_findings(self, publish_artifact: PublishArtifact) -> None:
        """Publish a challenge, an open question and a review finding, so every fold has work."""
        _publish_json(publish_artifact, "plan-challenges", {"challenges": [{"id": "C-1"}]})
        _publish_json(publish_artifact, "plan-questions", _plan_questions(1))
        _publish_json(publish_artifact, "plan-findings", {"findings": [{"id": "F-1"}]})

    @pytest.mark.usefixtures("questions_and_findings")
    def test_multi_slice_plan_makes_no_agent_call_for_the_one_slice_phasing(self, run: _Run) -> None:
        run.record("one-slice", slice="", slices="2")

        assert run.agent_steps() == {
            "check",
            "plan",
            "plan-challenge",
            "fold-challenges",
            "fold-answers",
            "plan-walkthrough",
        }
        assert not run.reaches("slice-check-questions")
        assert not run.reaches("check-breakdown")
        assert run.reaches("label-epic")

    @pytest.mark.usefixtures("questions_and_findings")
    def test_one_slice_plan_phases_its_slice_in_the_run(self, run: _Run) -> None:
        run.record("one-slice", slice="S1", slices="1")

        assert run.agent_steps() == {
            "check",
            "plan",
            "plan-challenge",
            "fold-challenges",
            "fold-answers",
            "slice-plan",
            "plan-review",
            "fold-findings",
            "slice-fold-answers",
            "breakdown",
            "plan-walkthrough",
        }
        assert run.reaches("slice-answer-questions")
        assert run.reaches("slice-check-questions")
        assert not run.reaches("label-epic")

    def test_plan_with_no_open_question_asks_nothing_and_folds_nothing(
        self, run: _Run, publish_artifact: PublishArtifact
    ) -> None:
        _publish_json(publish_artifact, "plan-challenges", {"challenges": []})
        _publish_json(publish_artifact, "plan-questions", _plan_questions(0))
        _publish_json(publish_artifact, "plan-findings", {"findings": []})
        run.record("one-slice", slice="S1", slices="1")

        skipped = ("fold-challenges", "answer-questions", "fold-answers", "fold-findings")
        assert [step for step in skipped if run.reaches(step)] == []
        assert [step for step in ("slice-answer-questions", "slice-fold-answers") if run.reaches(step)] == []
        assert run.reaches("check-questions")
        assert run.reaches("slice-check-questions")

    def test_open_questions_the_guard_cannot_read_are_still_asked_and_folded(
        self, run: _Run, publish_artifact: PublishArtifact
    ) -> None:
        publish_artifact("plan-questions", {"plan.json": "not json"})

        assert run.reaches("answer-questions")
        assert run.reaches("fold-answers")

    def test_not_ready_verdict_holds_and_checks_again(self, run: _Run) -> None:
        run.record("gate", verdict="not_ready")

        assert run.reaches("hold")
        assert not run.ends("ready-loop")

    @pytest.mark.parametrize("verdict", ["ready", "needs_edits"])
    def test_verdict_no_person_must_settle_ends_the_ready_loop_without_holding(self, run: _Run, verdict: str) -> None:
        run.record("gate", verdict=verdict)

        assert not run.reaches("hold")
        assert run.ends("ready-loop")

    @pytest.mark.parametrize(("comment", "kept"), [("", False), ("Keep the old flag.", True)])
    def test_approver_instructions_are_kept_only_when_given(self, run: _Run, comment: str, kept: bool) -> None:
        run.record("approve-plan", selected="approve", comment=comment)

        assert run.reaches("keep-instructions") is kept


CODING_AGENT_STEPS: Final = {"code", "pr-prep", "review", "fix", "report", "pr-describe"}


class IssueCodeGuardTests:
    @pytest.fixture
    def run(self, artifact_store: ArtifactStore, publish_artifact: PublishArtifact) -> _Run:
        """Return a run of issue-code-v2 that picked slice S1 of an unready issue, with phase 1 next."""
        run = _Run(_load("issue-code-v2"), artifact_store)
        _publish_json(publish_artifact, "next-hold", {"holds": []})
        run.record("next", slice="S1", plan_session="blueprint_plan01")
        run.record("ready-state", ready="false")
        run.record("phase", phase="1", phase_file=".agentic/phases/phase-1.json", remaining="0")
        return run

    def test_slice_phased_at_planning_is_coded_without_planning_or_approval(self, run: _Run) -> None:
        run.record("phased-check", phased_session="blueprint_plan01")

        assert run.agent_steps() == {"check", *CODING_AGENT_STEPS}
        assert run.reaches("load-phased-plan")
        assert [step for step in ("check-questions", "check-breakdown", "approve-plan") if run.reaches(step)] == []

    def test_slice_not_phased_is_planned_reviewed_and_approved(
        self, run: _Run, publish_artifact: PublishArtifact
    ) -> None:
        _publish_json(publish_artifact, "plan-questions", _plan_questions(1))
        _publish_json(publish_artifact, "plan-findings", {"findings": [{"id": "F-1"}]})
        run.record("phased-check", phased_session="")

        planning = {"plan", "plan-review", "fold-findings", "fold-answers", "breakdown", "plan-walkthrough"}
        assert run.agent_steps() == {"check", *planning, *CODING_AGENT_STEPS}
        assert run.reaches("answer-questions")
        assert run.reaches("approve-plan")
        assert not run.reaches("load-phased-plan")

    def test_issue_ready_as_it_is_skips_the_ready_check_and_its_label(self, run: _Run) -> None:
        run.record("ready-state", ready="true")

        assert [step for step in ("check", "hold", "approve", "apply") if run.reaches(step)] == []

    def test_unready_issue_is_checked_and_its_label_approved(self, run: _Run) -> None:
        run.record("gate", verdict="ready")

        assert [step for step in ("check", "approve", "apply") if run.reaches(step)] == ["check", "approve", "apply"]
        assert not run.reaches("hold")
        assert run.ends("ready-loop")

    def test_slice_waiting_on_a_person_holds_and_picks_again(
        self, artifact_store: ArtifactStore, publish_artifact: PublishArtifact
    ) -> None:
        run = _Run(_load("issue-code-v2"), artifact_store)
        _publish_json(publish_artifact, "next-hold", {"holds": [{"id": "S2", "label": "S2 waits on #12"}]})
        run.record("next")

        assert run.reaches("next-hold")
        assert not run.ends("pick-loop")

    def test_picked_slice_ends_the_pick_loop_without_holding(self, run: _Run) -> None:
        assert not run.reaches("next-hold")
        assert run.ends("pick-loop")

    def test_iteration_after_every_phase_is_done_runs_only_the_gate(self, run: _Run) -> None:
        run.record("phase", phase="done", phase_file="", remaining="0")
        run.record("verify")

        assert not run.reaches("code")
        assert run.ends("code-loop")

    def test_coded_last_phase_that_passes_the_gate_ends_the_code_loop(self, run: _Run) -> None:
        run.record("code", status="proposed_patch")
        run.record("verify")

        assert run.reaches("code")
        assert run.ends("code-loop")

    @pytest.mark.parametrize(
        ("review", "ends"),
        [
            pytest.param('{"findings": []}', True, id="no-findings"),
            pytest.param('{"findings": [{"severity": "NIT"}]}', True, id="nits-only"),
            pytest.param('{"findings": [{"severity": "WARNING"}]}', False, id="warning"),
            pytest.param('{"action": "needs_human", "findings": [{"severity": "BLOCKER"}]}', True, id="needs-human"),
            pytest.param("not json", False, id="unreadable"),
        ],
    )
    def test_review_loop_ends_once_no_finding_an_agent_can_fix_remains(
        self, run: _Run, publish_artifact: PublishArtifact, review: str, ends: bool
    ) -> None:
        publish_artifact("review-findings", {"review.json": review})
        run.record("fix-verify", changed="false")

        assert run.ends("review-loop") is ends
