# Investigate one delivery ticket (`ship/investigate`)

Resolve the specific causal or boundary question identified during routing or implementation in the single-ticket `ship` flow, write `.agentic/ship/investigate.md` and `.agentic/ship/stage.json`, and stop. Do not apply the production fix or mutate GitHub state during investigation.

## Inputs

Read from disk:
- `.agentic/ship/contract.json` (`issue`, `pull_request`, `head_sha`)
- `.agentic/ship/progress.json` (`answered_questions`, `history`, `last_validation`, `last_review`)
- `.agentic/ship/assess.md` (and `.agentic/ship/implement.md` or `.agentic/review.md` when routed from a later stage)
- Current checkout code, callers, and tests governed by [AGENTS.md](../../../../../AGENTS.md)

## Investigation rules

- Focus strictly on the stated causal question, reproduction path, or boundary ambiguity.
- Trace actual execution paths, callers, and tests in the checkout; you may run focused read-only queries or targeted pytest commands to confirm root cause. Leave the working tree clean (`git status --porcelain` outside `.agentic/` and `.dovo/run` must remain clean) before finishing.
- Settle ordinary technical questions from code evidence rather than escalating them to a human. Escalate with `ask` only when root-cause evidence reveals a genuine product or scope trade-off that changes an accepted outcome or constraint.

## Dispositions

Choose one disposition accepted by [ship_state.py](../../../scripts/sdlc/ship/ship_state.py) for `stage: "investigate"`:

| Disposition | When to choose it |
|---|---|
| `implement` | Root cause and fix boundary are proven and ready for direct implementation via [ticket-implement.md](ticket-implement.md). |
| `plan` | Root cause is proven, and the fix requires an explicit single-PR design plan via [ticket-plan.md](ticket-plan.md). |
| `assess` | Investigation evidence changes the premise of the route (for example, proving the issue is already fixed on `main`) so [ticket-route.md](ticket-route.md) should re-evaluate. |
| `ask` | Evidence uncovers an unsettled human trade-off or contract change. Include `questions` in `.agentic/ship/stage.json`. |
| `blocked` | Evidence proves the ticket is blocked by an external prerequisite or cannot fit one coherent pull request (`split_needed`). |

## Required outputs

1. Write `.agentic/ship/investigate.md` with:
   - The question investigated and the verified root cause (`path:line` citations and any reproduction command output)
   - The responsible fix boundary and affected callers/tests
   - The handoff to `implement`, `plan`, `assess`, `ask`, or `blocked`
2. Write `.agentic/ship/stage.json`:

```json
{
  "stage": "investigate",
  "disposition": "implement",
  "summary": "One-sentence summary of the verified root cause and next stage.",
  "body_sha": "<contract.issue.body_sha>",
  "head_sha": "<git rev-parse HEAD>",
  "report_path": ".agentic/ship/investigate.md"
}
```
