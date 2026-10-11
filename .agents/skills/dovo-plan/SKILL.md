---
name: dovo-plan
description: >-
  Formulate a phased, invariant-safe implementation plan for a Dovo CLI change
  before executing code changes. Audits repository constraints against REVIEW_CHECKLIST.json,
  grounds artifacts in the tree, declares per-FR test stubs and what the change deletes,
  and writes the plan directly to .agentic/plan.md (with citations split into
  .agentic/evidence.md when non-trivial). Invoked as /dovo-plan [<issue-number>].
disable-model-invocation: true
---

# dovo-plan

Produce an implementation plan precise enough that a different agent with no memory of the issue could implement it correctly, and a reviewer could audit the diff against it line by line. Do not write or modify implementation code; the plan document is the entire deliverable.

[docs/agents/planning.md](../../../docs/agents/planning.md) is the authority for plan content and process — its steps, artifact checklist, code sample rules, and plan template are not repeated here. Every step is backed by a `planner_rules` entry in [rules_spec.yaml](../../../docs/agents/rules_spec.yaml), rendered for reference at [PLANNER_RULES.md](../../../docs/agents/PLANNER_RULES.md): each names a phase, a scope, and a deliverable contract that following planning.md's matching step already satisfies.

Plan when the issue adds or changes a command, DTO/`*Result` model, status enum, domain exception, service, entrypoint method, formatter, config key, schema field, or `core/db/` table. Skip only for a genuinely single-file, no-new-surface change.

## Hard boundaries

- **Never run tooling that mutates or validates.** No `dovo run tests`, no `dovo run quality`, no `pytest`, no `ruff`, no `basedpyright`, no `uv sync`, no `dovo` command. Read-only git (`git log`, `git diff`, `git show`, `git rev-parse`) and read-only `gh` (`gh issue view`, `gh repo view`) are the only commands you need.
- **Never edit `src/` or `tests/`.** The plan document (`.agentic/plan.md`) is the entire deliverable. No commits, no pushes, no PR state.
- **Never plan from memory of the codebase.** Every path, symbol, and signature in the plan comes from a file read this session.
- **Never plan a test whose contract is already pinned elsewhere** (`TEST-004`). A plan that grows the suite without naming what each new test uniquely pins is a failed plan.

## 1. Reset the workspace

```bash
rm -rf .agentic && mkdir -p .agentic
```

Clears the previous cycle's artifacts (`PLAN-002`) so a stale plan or review can never be read as current.

## 2. Ground and audit

Follow `docs/agents/planning.md` Steps 1-3: extract the issue's contract (or the user's request, absent an issue number), ground every touched surface live in the tree with `file:line` citations, name the pattern to mirror and every trap, and check the touched paths against `docs/agents/REVIEW_CHECKLIST.json` and `PLANNER_RULES.md`.

## 3. Write the plan

Structure `.agentic/plan.md` per planning.md's Step 3 artifact checklist and Step 4 template: Open Questions and GWT Scenarios leading the document, then contract, traps, ground truth and artifact inventory (pointers to `.agentic/evidence.md`, which carries the actual tables and citations), deletion ledger, phased FR sections with code samples and test stubs, and cross-cutting doc updates.

When the issue leaves a detail genuinely unspecified, choose the option consistent with the nearest existing pattern, record the rejected alternative, and append 🚨 to that line rather than stalling or inventing product behavior. Ask at most one clarifying question when a tradeoff genuinely needs confirmation first.

## 4. Hand off

Before saving, run the self-check list in `docs/agents/planning.md` and sweep `REVIEW_CHECKLIST.json` for every rule matching the touched paths (`PLAN-016`) — zero `BLOCKER` violations remaining, with no compliance table added to the plan itself.

Report the path(s) written, a one-paragraph summary, every numbered Open Questions entry restated in chat, and plainly that this was planning only: nothing was implemented, tested, committed, or pushed.

**A human reviews the plan before implementation.** Do not offer to start implementing in the same breath; stop and wait. `/dovo-code` consumes the approved `.agentic/plan.md`.
