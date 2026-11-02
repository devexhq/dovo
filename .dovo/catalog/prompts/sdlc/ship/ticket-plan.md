# Plan one delivery ticket (`ship/plan`)

Produce a proportional, executable single-PR plan for the current ticket in the `ship` flow, write `.agentic/ship/plan.md` (and `.agentic/plan.md` when helpful for review traceability) plus `.agentic/ship/stage.json`, and stop. Do not create GitHub sub-issues, post multi-slice work-plan comments, implement code, or pause for routine plan approval.

## Inputs

Read from disk:
- `.agentic/ship/contract.json` (`issue`, `pull_request`, `head_sha`)
- `.agentic/ship/progress.json` (`answered_questions`, `history`, `last_validation`, `last_review`)
- `.agentic/ship/assess.md` and `.agentic/ship/investigate.md` (when present)
- Repository rules and conventions in [AGENTS.md](../../../../../AGENTS.md) and [docs/agents/code-conventions.md](../../../../../docs/agents/code-conventions.md)

## Planning rules

- Read the code, callers, tests, and nearest exemplars needed to settle the design. Verify inherited claims against the current checkout and identify new files explicitly as new.
- Reuse settled decisions from the ticket and `.agentic/ship/progress.json` (`answered_questions`); never re-ask a question already answered.
- Choose the smallest coherent approach that satisfies every outcome (`O-n`), decision (`D-n`), binding value, and `Done when` condition while preserving R1–R18 in [AGENTS.md](../../../../../AGENTS.md).
- Internal implementation steps organize coding and verification inside one pull request; they are not separate GitHub issues or approval gates.
- Settle ordinary reversible implementation choices yourself. Use `ask` only when a choice changes an accepted outcome or constraint, or when a consequential product/design trade-off cannot be settled from code and repository rules.
- If the ticket cannot reasonably fit one coherent pull request, choose `blocked` (`split_needed`) and document the evidence and proposed ticket boundary correction in `.agentic/ship/plan.md` rather than silently trimming acceptance criteria.

## Dispositions

Choose one disposition accepted by [ship_state.py](../../../scripts/sdlc/ship/ship_state.py) for `stage: "plan"`:
- `implement`: Plan is complete and ready for [ticket-implement.md](ticket-implement.md). No routine human approval is requested.
- `ask`: A genuine scope or design trade-off requires a human answer before coding. Populate `questions` in `.agentic/ship/stage.json`.
- `blocked` (or `split_needed`): The ticket is blocked or requires splitting before implementation.

## Required outputs

1. Write `.agentic/ship/plan.md` containing:
   - Outcomes (`O-n`), decisions (`D-n`), binding values, and constraints being implemented
   - Chosen approach, settled technical decisions, and code evidence (`path:line`)
   - Ordered implementation and test steps anchored to real paths or explicitly new files
   - Mapping from each outcome/constraint to the code and tests that verify it
   - Any unresolved human decision (`ask`) or boundary split proposal (`blocked`)
2. Write `.agentic/ship/stage.json`:

```json
{
  "stage": "plan",
  "disposition": "implement",
  "summary": "One-sentence summary of the plan and readiness for implementation.",
  "body_sha": "<contract.issue.body_sha>",
  "head_sha": "<git rev-parse HEAD>",
  "report_path": ".agentic/ship/plan.md"
}
```
