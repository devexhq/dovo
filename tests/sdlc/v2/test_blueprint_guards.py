"""Tier 1 contract: the `when` guards, `if` blocks and loop `until` lists of the sdlc v2 blueprints, and the review loop feature-dev shares with them, decide which steps a run reaches.

Each blueprint is loaded from the catalog and every guard is evaluated by the engine's guard
functions, against the step outputs and the artifacts a run would have recorded by then.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest
import yaml

from dovo.common.redact import SecretRedactor
from dovo.core.artifacts.models import ArtifactProvenance
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.catalog.definitions.blueprint import BlueprintDefinition
from dovo.core.catalog.definitions.step import IfStepBlock, LoopStepBlock, StepDefinition
from dovo.core.interaction.services.resolve import resolve_request
from dovo.engine.executors.models import StepResult
from dovo.engine.guards import choose_branch, evaluate_guard, guard_skip
from dovo.engine.models import IfBranch
from tests.conftest import PublishArtifact
from tests.lint.astlib import REPO_ROOT

BLUEPRINTS_DIR: Final = REPO_ROOT / ".dovo" / "catalog" / "blueprints"

NOT_READY_HOLDS: Final = {"holds": [{"id": "G-1", "label": "G-1 (gate): blocker #12 is open. Fix: close it."}]}

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

    def published_after(self, step_id: str) -> set[str]:
        """Return the names of the artifacts the steps the run reaches after step_id, in blueprint order, publish."""
        ids = list(self._entries)
        later = ids[ids.index(step_id) + 1 :]
        return {
            spec.name
            for later_id in later
            if isinstance(entry := self._entries[later_id][0], StepDefinition) and self.reaches(later_id)
            for spec in entry.artifacts
        }

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


QUESTION_CHAINS: Final = [
    pytest.param("issue-plan-v2", "", id="issue"),
    pytest.param("issue-plan-v2", "slice-", id="one-slice"),
    pytest.param("issue-code-v2", "", id="code-slice"),
]


def _question_run(blueprint: BlueprintDefinition, store: ArtifactStore) -> _Run:
    """Return a run entering each planning chain rather than loading a previously phased plan."""
    run = _Run(blueprint, store)
    run.record("one-slice", slice="S1", slices="1")
    run.record("phased-check", phased_session="")
    return run


class QuestionPolicyGuardTests:
    @pytest.fixture
    def detailed_mixed_policy(self, artifact_store: ArtifactStore, tmp_path: Path) -> None:
        """Publish validated mixed-policy artifacts with enough evidence to exceed the prompt preview."""
        question = {
            "id": "OQ-1",
            "summary": "Routine recommendation",
            "assumed": "Reuse the existing convention.",
            "details": [f"Grounded context {n}." for n in range(5)],
            "challenged": True,
            "challenge_answer": "Use the grounded alternative.",
            "challenge_details": [f"Challenge evidence {n}." for n in range(5)],
            "recommendation": "challenge",
            "needs_human": False,
            "human_reason": "routine",
            "recommendation_grounding": [f"Repository evidence {n}." for n in range(5)],
            "alternatives": ["Keep the old behavior."],
            "phases": [1],
            "blocks": [],
        }
        required = [
            {
                **question,
                "id": f"OQ-{n}",
                "summary": f"Required choice {n}",
                "challenge_answer": f"Choose required alternative {n}.",
                "needs_human": True,
                "human_reason": "tradeoff",
            }
            for n in range(2, 12)
        ]
        source = tmp_path / ".agentic"
        source.mkdir()
        (source / "plan.json").write_text(
            json.dumps({"open_questions": [*required, question], "decisions": []}), encoding="utf-8"
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / ".dovo/catalog/scripts/sdlc/v2/plan.py"),
                "questions",
                "--plan",
                ".agentic/plan.json",
                "--summary",
                ".agentic/plan-summary.md",
                "--policy",
                ".agentic/question-policy.json",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert (completed.returncode, completed.stdout, completed.stderr) == (0, "", "")
        assert len((source / "plan-summary.md").read_text(encoding="utf-8").splitlines()) > 200
        for name, filename in [("question-policy", "question-policy.json"), ("plan-summary", "plan-summary.md")]:
            assert artifact_store.publish(
                tmp_path,
                name=name,
                path_glob=f".agentic/{filename}",
                retention_days=None,
                provenance=ArtifactProvenance(),
            ).ok

    @pytest.mark.parametrize(("blueprint", "prefix"), QUESTION_CHAINS)
    @pytest.mark.parametrize(("questions", "required"), [(0, 0), (1, 0), (2, 1)], ids=["empty", "routine", "mixed"])
    def test_question_policy_routes_each_planning_chain(
        self,
        artifact_store: ArtifactStore,
        publish_artifact: PublishArtifact,
        blueprint: str,
        prefix: str,
        questions: int,
        required: int,
    ) -> None:
        _publish_json(publish_artifact, "plan-questions", _plan_questions(questions))
        _publish_json(publish_artifact, "question-policy", {"required_questions": [{"id": "OQ-2"}] * required})
        run = _question_run(_load(blueprint), artifact_store)

        assert run.reaches(f"{prefix}answer-questions") is bool(required)
        assert run.reaches(f"{prefix}fold-answers") is bool(questions)
        assert run.reaches(f"{prefix}check-questions")

    @pytest.mark.parametrize(("blueprint", "prefix"), QUESTION_CHAINS)
    @pytest.mark.parametrize("policy", [None, "not json", "{}"], ids=["missing", "unreadable", "missing-list"])
    def test_unreadable_question_policy_never_skips_the_question_prompt(
        self,
        artifact_store: ArtifactStore,
        publish_artifact: PublishArtifact,
        blueprint: str,
        prefix: str,
        policy: str | None,
    ) -> None:
        if policy is not None:
            publish_artifact("question-policy", {"question-policy.json": policy})
        run = _question_run(_load(blueprint), artifact_store)

        assert run.reaches(f"{prefix}answer-questions")

    @pytest.mark.usefixtures("detailed_mixed_policy")
    @pytest.mark.parametrize(("blueprint", "prefix"), QUESTION_CHAINS)
    def test_mixed_prompt_uses_required_choices_and_publishes_complete_summary(
        self,
        artifact_store: ArtifactStore,
        blueprint: str,
        prefix: str,
    ) -> None:
        definition = _load(blueprint)
        steps = {entry.id: entry for entry, _ in _walk(list(definition.steps)) if isinstance(entry, StepDefinition)}
        summary = steps[f"{prefix}questions-summary"]
        ask = steps[f"{prefix}answer-questions"]
        fold = steps[f"{prefix}fold-answers"]
        check = steps[f"{prefix}check-questions"]
        assert ask.interaction is not None
        resolution = resolve_request(
            ask.id, ask.interaction, ask.interaction.message, artifact_store, SecretRedactor([])
        )

        assert resolution.ok
        assert resolution.request is not None
        request = resolution.request
        assert [(option.value, option.label) for option in request.options] == [
            (f"OQ-{n}", f"Required choice {n}") for n in range(2, 12)
        ]
        assert request.comment.policy == "required"
        assert "every required" in request.message
        assert "whether ticked or not" in request.message
        assert "override any routine answer by its OQ-id" in request.message
        assert request.artifact is not None
        assert "OQ-1" in "\n".join(request.artifact.preview)
        assert "OQ-2" in "\n".join(request.artifact.preview)
        assert any("OQ-1 |" in line and "Use the grounded alternative." in line for line in request.artifact.preview)
        assert {(spec.name, spec.path) for spec in summary.artifacts} == {
            ("plan-summary", ".agentic/plan-summary.md"),
            ("question-policy", ".agentic/question-policy.json"),
        }
        assert summary.run is not None
        assert "--policy .agentic/question-policy.json" in summary.run
        assert check.run is not None
        assert "--policy .agentic/question-policy.json" in check.run
        assert fold.prompt is not None
        assert f"steps.{prefix}answer-questions.outputs.selected || ''" in fold.prompt
        assert f"steps.{prefix}answer-questions.outputs.comment || ''" in fold.prompt


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
        _publish_json(publish_artifact, "question-policy", {"required_questions": [{"id": "OQ-1"}]})
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

    @pytest.mark.parametrize(("slice_id", "slices"), [("", "2"), ("S1", "1")], ids=["multi-slice", "one-slice"])
    def test_plan_fixed_at_the_checks_is_republished_on_every_path(self, run: _Run, slice_id: str, slices: str) -> None:
        run.record("one-slice", slice=slice_id, slices=slices)

        assert {"plan", "plan-questions"} <= run.published_after("check-slices")

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
        _publish_json(publish_artifact, "question-policy", {"required_questions": []})
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

    @pytest.mark.parametrize(
        ("holds", "asked"),
        [
            pytest.param('{"holds": []}', True, id="ready"),
            pytest.param(json.dumps(NOT_READY_HOLDS), False, id="not-ready-at-the-loop-ceiling"),
            pytest.param("not json", True, id="unreadable"),
        ],
    )
    def test_label_approval_is_asked_unless_findings_still_hold(
        self, run: _Run, publish_artifact: PublishArtifact, holds: str, asked: bool
    ) -> None:
        publish_artifact("issue-hold", {"issue-hold.json": holds})

        assert run.reaches("approve") is asked
        assert run.reaches("apply")

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
        _publish_json(publish_artifact, "question-policy", {"required_questions": [{"id": "OQ-1"}]})
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

    def test_unready_issue_is_checked_and_its_label_approved(
        self, run: _Run, publish_artifact: PublishArtifact
    ) -> None:
        _publish_json(publish_artifact, "issue-hold", {"holds": []})
        run.record("gate", verdict="ready")

        assert [step for step in ("check", "approve", "apply") if run.reaches(step)] == ["check", "approve", "apply"]
        assert not run.reaches("hold")
        assert run.ends("ready-loop")

    def test_unready_verdict_past_the_ready_loop_asks_no_approval_and_reaches_the_refusing_apply(
        self, run: _Run, publish_artifact: PublishArtifact
    ) -> None:
        _publish_json(publish_artifact, "issue-hold", NOT_READY_HOLDS)
        run.record("gate", verdict="not_ready")

        assert not run.reaches("approve")
        assert run.reaches("apply")

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

    def test_iteration_after_every_phase_is_done_runs_only_the_checks(self, run: _Run) -> None:
        run.record("phase", phase="done", phase_file="", remaining="0")
        run.record("verify")

        assert not run.reaches("code")
        assert run.ends("code-loop")

    def test_coded_last_phase_that_passes_its_checks_ends_the_code_loop(self, run: _Run) -> None:
        run.record("code", status="proposed_patch")
        run.record("verify")

        assert run.reaches("code")
        assert run.ends("code-loop")


@pytest.mark.parametrize("blueprint", ["issue-code-v2", "feature-dev"])
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
    artifact_store: ArtifactStore, publish_artifact: PublishArtifact, blueprint: str, review: str, ends: bool
) -> None:
    run = _Run(_load(blueprint), artifact_store)
    publish_artifact("review-findings", {"review.json": review})
    run.record("fix-verify", changed="false")

    assert run.ends("review-loop") is ends
