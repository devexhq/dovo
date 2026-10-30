# fold-answers

Settle every open question of a plan written by `issue-plan`: a slice plan, or in issue mode the plan that slices an issue. The human has just been asked which open questions to answer: each one they ticked is settled by their answer, and each one they left is settled by the answer the plan assumed. After this prompt the plan has no open questions, only decisions that say who settled them. The blueprint's next step, `check-questions`, stops the run if any question is left open or any decision is malformed.

The blueprint gives you the ticked question ids, the human's answers (one comment, usually written as `OQ-1: <answer>`) and the slice id, or says the plan slices the issue in issue mode. The issue the decisions are posted to is the slice's issue, the one the blueprint says the slice is worked on (its own issue when it has one, otherwise the sliced issue), or in issue mode the issue itself.

## Hard boundaries

- **Change only `.agentic/plan.md` and `.agentic/plan.json`.** Never implement code, edit the issue, commit or push.
- **Never change `plan.json` `contract` or `slices`** other than this slice's `phases`. An answer that departs from the contract is planned as a named departure in this slice's phases, and the slice's report amends the contract when the slice lands. In issue mode the contract and the slices are the plan: rewrite them against an answer like any other part of it.
- **Re-read from disk.** Read both plan files fresh in this session.

## 1. Settle each question

For every entry of `plan.json` `open_questions`, in order:

- **Ticked:** rewrite the plan against the human's answer, as if it had been the plan's answer from the start: every phase, test line and trap it changes (in issue mode, every slice, contract section and Traceability row). The decision is the answer as planned, with `settled_by` `"human"` and `answer` holding the human's words for that question, unedited. If the comment gives no answer for a ticked question, settle nothing in either file and report which question has no answer. `check-questions` then stops the run with that question still open.
- **Not ticked:** the plan already holds the assumed answer; remove its "Reply '…' for …" text. The decision is the question's `assumed` answer, with `settled_by` `"assumed_accepted"` and no `answer`.

## 2. Record the decisions

Append one entry per question to `plan.json` `decisions`, keeping the entries already there (the planner's own departures). Each entry has the shape [issue-plan.md](issue-plan.md) Step 5 defines:

- `id`: the next free `D-<n>`, continuing after the highest decision id in the issue the decisions are posted to (its Decisions, `gh issue view <n> --json body`), in `.agentic/prior-decisions.json` and in `decisions`.
- `was`: the question's `OQ-<n>` id.
- `decision`: one sentence stating the choice itself.
- `rejected`: the answers not taken, one sentence each: the question's `alternatives`, minus the human's answer, plus the assumed answer when the human chose another. `[]` when there were none.
- `phases`: the question's `phases`.
- `date`: today, as `YYYY-MM-DD` (`date +%F`).
- `replaces`: the question's `replaces`, copied unchanged, when it has one; leave the key out otherwise.

Then set `open_questions` to `[]`.

## 3. Rewrite the plan's markdown

In `.agentic/plan.md`, under What you're approving, replace the **Open questions:** block with a **Decisions:** block, one entry per decision this prompt added, in id order:

```markdown
**Decisions:**

> **D-<n> (was OQ-<m>, settled by the human | assumed answer accepted): <the decision>**
> - **Answer:** <the human's words> (only when settled by the human)
> - **Why:** <the question's reasons that still hold, one fact per bullet, each with its `path:line`>
> - **Rejected:** <each answer not taken>
> - **Phases:** <the phases it shapes> (in issue mode, **Slices:** <the slices it blocks>)
```

Write "None." under **Decisions:** when the plan had no open questions. Replace every `OQ-<m>` reference elsewhere in the plan, such as a phase's "[See OQ-<m>.]", with its `D-<n>`. Keep `plan.json` agreeing with `plan.md`, and in the shape [issue-plan.md](issue-plan.md) Step 5 defines for the plan's mode.

## 4. Hand off

Report each decision added, as its id, former question id and how it was settled, and plainly that nothing was implemented, committed or pushed.
