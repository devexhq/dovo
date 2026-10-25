---
name: dovo-plan-challenge
description: >-
  Challenge the design of a Dovo CLI plan before any code is written: argue
  for the plan first, then test its architecture, logic and approach, and the
  issue requirements behind them, against the code, its unwritten patterns,
  its failure paths and the other open issues in the milestone. Writes at most
  five evidence-backed challenges, each routed to be folded or asked, to
  .agentic/plan-challenge.md and .agentic/plan-challenge.json. Invoked as
  /dovo-plan-challenge [<plan-path>].
disable-model-invocation: true
---

# dovo-plan-challenge

Challenge a plan written by `/dovo-plan`. `/dovo-plan-review` checks that the plan does what its issue says without breaking a rule; this skill asks whether the plan, and the issue behind it, are the right design. Do not repeat the review: grounding, coverage, R13 matrices and rule-by-rule checks are its job, not yours.

Output goes to `.agentic/plan-challenge.md`, paired with `.agentic/plan-challenge.json`. The blueprint folds challenges routed `fold` into the plan and asks a human about those routed `ask`, so every challenge you write costs either a plan change or a human decision. "No challenges" is a normal result for a plan whose design holds.

## Hard boundaries

- **Never edit the plan, the issue, `src/` or `tests/`, and never commit or push.** Report challenges and hand off.
- **Never run tests or tooling.** Read-only `git`, read-only `gh`, `rg` and file reads are enough.
- **Never read `.agentic/plan-review.md` or `.agentic/plan-review.json`.** An independent challenge is the point; the review's framing is not yours to inherit.
- **Re-read from disk.** Every file you reason about is read fresh in this session, never recalled.
- **Every challenge cites evidence**: a `path:line`, a sha or an issue number. A challenge without one is an opinion; drop it.

## 1. Resolve the input

The plan is `<plan-path>` when given, otherwise `.agentic/plan.md`. If it is missing, stop and say so. Read it in full, every file it links to, and `plan.json` beside it. Take the issue number from the `# Issue #<n>:` header and read it with `gh issue view <n> --json number,title,body,milestone`. When the issue has a milestone, list its other open issues with `gh issue list --milestone "<title>" --state open --json number,title,body`; the planner never reads them, so you are the only step that does.

## 2. Challenge from a fresh context

If this session wrote or reviewed the plan, delegate the challenge to a fresh subagent. Give it the Hard boundaries above as its first lines, the plan path and the issue number, and nothing else. If no subagent can be launched, say so in the report's **Provenance** line.

## 3. Argue for the plan first

Before challenging anything, write two or three sentences under **Steelman**: the approach the plan takes and the strongest reason it is right. A challenge must beat that reason, not a weaker version of it.

## 4. Challenge through these lenses

Read the code the plan changes, its callers and its nearest neighbours. Then test the plan, and the issue requirements it follows, through each lens:

- **Unwritten patterns** (`unwritten_pattern`): the plan departs from what the exemplars in `AGENTS.md` or sibling modules do, where no rule says so. Cite the pattern and where the plan departs from it.
- **Failure paths** (`failure_path`): what happens on an unexpected exception, a partial write, a timeout, a retry, an interrupted run or a resume. Trace the path through the planned code; a plan that only describes the happy path has not decided the rest.
- **Milestone fit** (`milestone_fit`): a shape this plan settles that another open issue in the milestone must undo, or a decision another issue already depends on. Cite the issue and the sentence.
- **Simpler alternative** (`simpler_alternative`): the same contract met with fewer new symbols, files, fields or phases. Name the alternative concretely.
- **Unverified assumptions** (`unverified_assumption`): a claim about current behaviour the plan states without a citation, or with one that does not show it. Check it against the code.
- **Issue requirements** (`issue_requirement`): an issue change that contradicts its own goal, or a guard that freezes wording nothing relies on or excludes behaviour of the foundation the issue adopts (`.agents/skills/dovo-issue/SKILL.md` Core Principles 5). A challenge to an issue change is always routed `ask`.

## 5. Keep only what clears the bar

A challenge states:

- **Claim**: what is wrong with the design, in one sentence.
- **Evidence**: the `path:line`, sha or issue that shows it.
- **Failure**: the concrete scenario that goes wrong, or the rework it forces, if the plan ships as written.
- **Alternative**: the design you would plan instead, concrete enough to fold.
- **Route**: `fold` when the alternative stays within the issue's changes, is clearly better on the evidence and needs no product decision; `ask` when it is a trade-off, departs from an issue change or guard, or needs a decision only a human can make.

Keep at most five, ranked by the cost of being wrong. Drop a challenge whose failure is cosmetic, whose alternative is a matter of taste, or that only restates a rule check.

When a challenge exists because a pattern lives only in the code, add a **Rule proposal**: the one sentence for `AGENTS.md` or a doc under `docs/agents/` that would let a rule check catch it next time.

## 6. Write the report

Write `.agentic/plan-challenge.md`, overwriting any earlier one, and echo the challenges in chat:

```markdown
## dovo-plan-challenge

**Plan:** `<plan path>`, grounded at `<sha>`
**Provenance:** fresh session | same session that wrote or reviewed the plan

### Steelman

<two or three sentences>

### Challenges

| ID | Lens | Plan section | Claim | Evidence | Failure | Alternative | Route |
|---|---|---|---|---|---|---|---|
| C1 | failure_path | Phase 2 instructions | ... | `path:line` | ... | ... | ask |

### Rule proposals
- C1: <the sentence, and the file it belongs in>
```

An empty section keeps its heading with the line `none`.

## 7. Emit the machine-readable challenges

Write `.agentic/plan-challenge.json` alongside the markdown, overwriting any earlier one, agreeing with `plan-challenge.md` row for row:

```json
{
  "plan": ".agentic/plan.md",
  "grounded_at": "603dd408",
  "provenance": "fresh_session",
  "steelman": "The plan stores the discard flag on the session row so list filtering stays one SQL query.",
  "challenges": [
    {
      "id": "C1",
      "lens": "failure_path",
      "section": "Phase 2 instructions",
      "claim": "The plan deletes the session directory before it commits the row update, so an interrupted discard leaves a row pointing at nothing.",
      "evidence": "src/dovo/core/sessions/services/discard.py:41",
      "failure": "A run killed between the two writes shows the session in dovo session list, and dovo session show fails on it.",
      "alternative": "Commit the row update first and delete the directory after; a leftover directory is reclaimed by prune.",
      "route": "fold",
      "rule_proposal": null
    }
  ]
}
```

- `provenance` is `fresh_session` or `same_session`.
- `lens` is one of the six names in step 4; `route` is `fold` or `ask`; `rule_proposal` is `null` when there is none.
- `challenges` is `[]` when the report says `none`.

## 8. Hand off

Report the report path and the number of challenges by route. Do not edit the plan: the blueprint folds `fold` challenges and turns `ask` challenges into Open Questions for a human.
