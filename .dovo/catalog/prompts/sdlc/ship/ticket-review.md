# Review one delivery ticket (`ship/review`)

Review the committed change at `HEAD` against `.agentic/ship/contract.json`, failure paths, regression risks, and [AGENTS.md](../../../../../AGENTS.md) rules R1–R18. Write `.agentic/review.md`, `.agentic/review.json`, and `.agentic/ship/stage.json`, and stop. Never edit tracked files, stage, commit, or push during review.

## Inputs

Read from disk:
- `.agentic/ship/contract.json` (`issue`, `pull_request`, `base_sha`, `head_sha`)
- `.agentic/ship/progress.json` (`answered_questions`, `last_validation`, `last_review`, `review_rounds`)
- `.agentic/ship/plan.md` and `.agentic/ship/implement.md` (when present)
- Previous `.agentic/review.json` when `review_rounds > 0`
- Current git diff (`git diff origin/main...HEAD`) and `git rev-parse HEAD`
- [AGENTS.md](../../../../../AGENTS.md), [docs/agents/code-conventions.md](../../../../../docs/agents/code-conventions.md), and [docs/agents/architecture/README.md](../../../../../docs/agents/architecture/README.md)

## Review priorities

1. **Correctness and failure paths:** Bugs, regressions, unhandled edge cases, partial state writes, or silent error swallowing in the diff.
2. **Contract coverage:** Every outcome (`O-n`), decision (`D-n`), binding value, and `Done when` condition in `.agentic/ship/contract.json` (plus any settled `answered_questions` in `.agentic/ship/progress.json`) is met by the code and verified by meaningful assertions in tests. An outcome a person can observe from the installed binary is also verified by an end-to-end scenario under `e2e/` (R13); a missing or stale scenario is a rule violation.
3. **Repository rules and docs:** Compliance with R1–R18 in [AGENTS.md](../../../../../AGENTS.md), [docs/agents/code-conventions.md](../../../../../docs/agents/code-conventions.md), and required documentation updates.

## Dispositions

Choose one disposition accepted by [ship_state.py](../../../scripts/sdlc/ship/ship_state.py) for `stage: "review"`:

| Disposition | Corresponding `review.json` `verdict` / `action` | When to choose it |
|---|---|---|
| `ready` | `verdict: "APPROVE"`, `action: "none"`, `counts.blocking: 0` | Zero blocking findings and every contract requirement is `met`. |
| `changes_requested` | `verdict: "CHANGES_REQUIRED"`, `action: "fix"`, `counts.blocking > 0` | One or more blocking bugs, unmet contract items, or rule violations that [ticket-implement.md](ticket-implement.md) can fix within scope. |
| `plan` | `verdict: "CHANGES_REQUIRED"`, `action: "replan"` | A blocking finding requires revising the design in [ticket-plan.md](ticket-plan.md). |
| `investigate` | `verdict: "CHANGES_REQUIRED"`, `action: "fix"` | A blocking finding requires targeted root-cause investigation in [ticket-investigate.md](ticket-investigate.md). |
| `ask` | `verdict: "CHANGES_REQUIRED"`, `action: "needs_human"` | Resolving a finding requires a human scope or trade-off decision. Include `questions` in `.agentic/ship/stage.json`. |
| `blocked` | `verdict: "CHANGES_REQUIRED"`, `action: "needs_human"` | The change cannot be completed without an external blocker or ticket split. |

## Required outputs

1. Write `.agentic/review.md` and `.agentic/review.json` in the exact format validated by `scripts/publish_review.py`:
   - `.agentic/review.md` must hold these two lines, with the verdict and action on the same line, or publishing the review fails:

     ```markdown
     **Verdict:** <APPROVE|CHANGES REQUIRED> · **Action:** <none|fix|replan|needs_human>
     **Reviewed commit:** `<full HEAD sha>`
     ```
   - `.agentic/review.json` must include `head` (full `HEAD` SHA), `verdict` (`"APPROVE"` or `"CHANGES_REQUIRED"`), `action` (`"none"`, `"fix"`, `"replan"`, or `"needs_human"`), `round` (`review_rounds + 1`), `counts` (`blocking`, `warnings`, `suggestions`, `nits`), `findings` (each with `id`, `severity`, `path`, `start_line`, `line`, `rule`, `summary`, `issue`, `fix`, `also_affects`), and `coverage`.
   - Each finding's `severity` is exactly `"BLOCKER"`, `"WARNING"`, `"SUGGESTION"` or `"NIT"`, in uppercase. `rule` is an R-id, `"conventions"`, `"architecture"` or `null`. A one-line location sets `line` to that line and `start_line` to `null`. A range sets `start_line` to its first line and `line` to its last, so `start_line` is below `line`. A finding with no line sets both to `null`. `summary`, `issue` and `fix` are non-empty strings, and `also_affects` is a list of `path:line` or `path:start-end` entries (`[]` when there are none).
   - Before writing `.agentic/ship/stage.json` with `ready`, run `uv run python scripts/publish_review.py --check` and fix every refusal it prints. The recorder runs the same check on a `ready` review and sends a refused one back to this stage.
   - When `last_review.status` in `.agentic/ship/progress.json` is `rejected` for the current `HEAD`, its `summary` is the publisher's refusal. Correct `.agentic/review.json` and `.agentic/review.md` for the same `HEAD` and record the review again.
2. Write `.agentic/ship/stage.json` tying the review verdict to the exact `body_sha` and `head_sha`:

```json
{
  "stage": "review",
  "disposition": "ready",
  "summary": "APPROVE round 1; all contract outcomes and repository rules satisfied.",
  "body_sha": "<contract.issue.body_sha>",
  "head_sha": "<git rev-parse HEAD>",
  "report_path": ".agentic/review.md"
}
```
