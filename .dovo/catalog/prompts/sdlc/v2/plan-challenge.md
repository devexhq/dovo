# plan-challenge

Challenge an issue plan written by `issue-plan` in issue mode: the contract and the slices, with no phases. This prompt asks whether the plan, and the issue behind it, are the right design. It runs once per issue, never on a slice plan: `issue-plan-review` reviews each slice's phases against the decisions settled here. No review runs on the issue plan itself: its outcome coverage and slice sizes are checked by `plan.py check-slices`, and grounding, R13 matrices and rule-by-rule checks belong to each slice's review, so leave them out.

Output goes to `.agentic/plan-challenge.json`. The blueprint folds challenges routed `fold` into the plan and asks a human about those routed `ask`, so every challenge you write costs either a plan change or a human decision. "No challenges" is a normal result for a plan whose design holds.

## Hard boundaries

- **Never edit the plan, the issue, `src/` or `tests/`, and never commit or push.** Report challenges and hand off.
- **Never run tests or tooling.** Read-only `git`, read-only `gh`, `rg` and file reads are enough.
- **Re-read from disk.** Every file you reason about is read fresh in this session, never recalled.
- **Every challenge cites evidence**: a `path:line`, a sha or an issue number. Drop a challenge without one.

## 1. Resolve the input

The plan is `<plan-path>` when given, otherwise `.agentic/plan.md`. If it is missing, stop and say so. Read it in full, every file it links to, and `plan.json` beside it. Take the issue number from the `# Issue #<n>:` header and read it with `gh issue view <n> --json number,title,body,milestone`. When the issue has a milestone, list its other open issues with `gh issue list --milestone "<title>" --state open --json number,title,body`; the planner never reads them, so you are the only step that does.

## 2. Challenge from a fresh context

If this session wrote or reviewed the plan, delegate the challenge to a fresh subagent. Give it the Hard boundaries above as its first lines, the plan path and the issue number, and nothing else. If no subagent can be launched, set `provenance` to `same_session`.

## 3. Argue for the plan first

Before challenging anything, write two or three sentences as the `steelman`: the approach the plan takes and the strongest reason it is right. A challenge must beat that reason, not a weaker version of it.

## 4. Challenge through these lenses

Read the code the plan changes, its callers and its nearest neighbours. Then test the plan, and the issue requirements it follows, through each lens:

- **Unwritten patterns** (`unwritten_pattern`): the plan departs from what the exemplars in `AGENTS.md` or sibling modules do, where no rule says so. Cite the pattern and where the plan departs from it.
- **Failure paths** (`failure_path`): what happens on an unexpected exception, a partial write, a timeout, a retry, an interrupted run or a resume. Trace the path through the planned code; a plan that only describes the happy path has not decided the rest.
- **Milestone fit** (`milestone_fit`): a shape this plan settles that another open issue in the milestone must undo, or a decision another issue already depends on. Cite the issue and the sentence.
- **Simpler alternative** (`simpler_alternative`): the same contract met with fewer new symbols, files, fields or phases. Name the alternative concretely.
- **Unverified assumptions** (`unverified_assumption`): a claim about current behaviour the plan states without a citation, or with one that does not show it. Check it against the code.
- **Issue requirements** (`issue_requirement`): an outcome or decision that contradicts the issue's own Why, or a decision or binding value that freezes something no person chose, such as incidental wording or an internal shape the template's checklist would leave out (`.dovo/catalog/prompts/sdlc/v2/templates/issue.md`); on a legacy issue, a guard that does the same or excludes behaviour of the foundation the issue adopts. A challenge to an issue's outcome or decision is always routed `ask`.

## 5. Keep only what clears the bar

A challenge states:

- **Claim**: what is wrong with the design, in one sentence.
- **Evidence**: the `path:line`, sha or issue that shows it.
- **Failure**: the concrete scenario that goes wrong, or the rework it forces, if the plan ships as written.
- **Alternative**: the design you would plan instead, concrete enough to fold.
- **Route**: `fold` when the alternative stays within the issue's changes, is clearly better on the evidence and needs no product decision; `ask` when it is a trade-off, departs from an issue change or guard, or needs a decision only a human can make.

Keep at most five, ranked by the cost of being wrong. Drop a challenge whose failure is cosmetic, whose alternative is a matter of taste, or that only restates a rule check.

When a challenge exists because a pattern lives only in the code, add a **Rule proposal**: the one sentence for `AGENTS.md` or a doc under `docs/agents/` that would let a rule check catch it next time, and the file it belongs in.

## 6. Write the challenges

Write `.agentic/plan-challenge.json`, overwriting any earlier one, and echo the challenges in chat:

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
      "section": "Phase 2 Code",
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
- `id` is `C1`, `C2` and so on; `section` is the plan section the challenge concerns.
- `challenges` is `[]` when no challenge clears the bar.

## 7. Hand off

Report the file path and the number of challenges by route. Do not edit the plan: the blueprint folds `fold` challenges and turns `ask` challenges into open questions in `plan.json` for a human.
