# Route one delivery ticket (`ship/assess`)

Assess what reasoning this ticket still needs in the single-ticket `ship` flow, write `.agentic/ship/assess.md` and `.agentic/ship/stage.json`, and stop. Do not perform a full causal investigation, write code, or mutate GitHub issues or pull requests during assessment.

## Inputs

Read from disk:
- `.agentic/ship/contract.json` (`issue.number`, `issue.title`, `issue.body`, `issue.body_sha`, `pull_request`, `head_sha`)
- `.agentic/ship/progress.json` (`answered_questions`, `history`, `last_validation`, `last_review`, `review_rounds`)
- Prior stage reports in `.agentic/ship/` when present (`.agentic/ship/investigate.md`, `.agentic/ship/plan.md`, `.agentic/ship/implement.md`)
- Current repository checkout (`git rev-parse HEAD`, `git status --porcelain`) and [AGENTS.md](../../../../../AGENTS.md)

Use source code for present behavior and the ticket contract (plus any settled `answered_questions` in `.agentic/ship/progress.json`) for desired behavior. Record material disagreements rather than silently choosing whichever source is convenient.

## Route selection

Check only enough to establish whether the requested outcome is still owed, prerequisites exist on the current base, the ticket communicates a usable outcome and acceptance boundary, and an existing pull request already carries part or all of the change.

Choose one disposition accepted by [ship_state.py](../../../scripts/sdlc/ship/ship_state.py):

| Disposition | When to choose it |
|---|---|
| `implement` | Outcome, acceptance, responsible boundary, and approach are clear enough to implement directly (or after a completed investigation/plan) without a material untested assumption. |
| `investigate` | A causal question about current behavior or the responsible fix boundary remains. State that exact question in `.agentic/ship/assess.md` for [ticket-investigate.md](ticket-investigate.md). |
| `plan` | Outcome is settled, but a material design or interaction choice needs an explicit single-PR plan before coding. State the design focus for [ticket-plan.md](ticket-plan.md). |
| `validate` | An existing pull request checkout or resumed run already contains the implementation at `HEAD` and needs validation next. |
| `review` | Validation has already passed at current `HEAD` and the change is ready for review. |
| `ask` | Intent is contradictory, an owner-level trade-off is unsettled, or a decision changes an accepted outcome or constraint. Populate `questions` in `.agentic/ship/stage.json`. |
| `no_action` | Verified code and test evidence on the current checkout proves the requested outcome already holds. Cite the proof; do not close the issue. |
| `blocked` | A required prerequisite is missing on the current base, an external action prevents progress, or the ticket is too broad for one coherent pull request (`split_needed`). State the exact blocker or proposed split. |

A clear small feature can go straight to `implement`; a small bug with an unknown root cause should route to `investigate`. Size alone chooses no route, and missing internal implementation details do not make a ticket unready.

## Required outputs

1. Write `.agentic/ship/assess.md` containing:
   - The chosen disposition and 1–3 sentence rationale
   - The `issue.number`, `body_sha`, and current `HEAD` SHA inspected
   - Decisive code/test evidence and any existing PR state
   - Preserved acceptance items (`O-n`), decisions (`D-n`), binding values, and settled `answered_questions`
   - Specific causal question (when `investigate`), design focus (when `plan`), or blocker/no-action proof
2. Write `.agentic/ship/stage.json` matching the current `body_sha` from `.agentic/ship/contract.json` and current `git rev-parse HEAD`:

```json
{
  "stage": "assess",
  "disposition": "implement",
  "summary": "One-sentence summary of the routing decision and evidence.",
  "body_sha": "<contract.issue.body_sha>",
  "head_sha": "<git rev-parse HEAD>",
  "report_path": ".agentic/ship/assess.md"
}
```

When `disposition` is `"ask"`, include a non-empty `questions` array where each item has `id`, `question`, `evidence`, `recommendation`, `alternatives`, and `affected_scope`.
