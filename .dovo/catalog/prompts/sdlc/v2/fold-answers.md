# fold-answers

Settle every open question of a plan written by `issue-plan`: a slice plan, or in issue mode the plan that slices an issue. The human has just been asked which open questions to answer: each one they ticked is settled by their answer, and each one they left is settled by the planner's recommended answer: the assumed one, or the challenge's. After this prompt the plan has no open questions, only decisions that say who settled them. The blueprint's next step, `check-questions`, stops the run if any question is left open or any decision is malformed.

The blueprint gives you the ticked question ids, the human's answers (one comment, usually written as `OQ-1: <answer>`) and the slice id, or says the plan slices the issue in issue mode. The plan's issue is the slice's issue, the one the blueprint says the slice is worked on (its own issue when it has one, otherwise the sliced issue), or in issue mode the issue itself. The decisions stay in plan.json; nothing posts them to the issue.

## Hard boundaries

- **Change only `.agentic/plan.md` and `.agentic/plan.json`.** Never implement code, edit the issue, commit or push.
- **Never change `plan.json` `contract` or `slices`** other than this slice's `phases`. An answer that departs from the contract is planned as a named departure in this slice's phases, and the slice's report amends the contract when the slice lands. In issue mode the contract and the slices are the plan: rewrite them against an answer like any other part of it.
- **Re-read from disk.** Read both plan files fresh in this session.

## 1. Settle each question

For every entry of `plan.json` `open_questions`, in order:

- **Ticked:** rewrite the plan against the human's answer, as if it had been the plan's answer from the start: every phase, test line and trap it changes (in issue mode, every slice and contract section). The decision is the answer as planned, with `settled_by` `"human"` and `answer` holding the human's words for that question, unedited. If the comment gives no answer for a ticked question, settle nothing in either file and report which question has no answer. `check-questions` then stops the run with that question still open.
- **Not ticked:** the question settles to its recommended answer, with no `answer`. When `recommendation` is `"assumed"`, the plan already holds it: the decision is the question's `assumed` answer, with `settled_by` `"assumed_accepted"`. When it is `"challenge"`, rewrite the plan against `challenge_answer` as for a ticked question; the decision is that answer, with `settled_by` `"challenge_accepted"`.

## 2. Record the decisions

Append one entry per question to `plan.json` `decisions`, keeping the entries already there (the planner's own departures). Each entry has the shape [issue-plan.md](issue-plan.md) Step 5 defines:

- `id`: the next free `D-<n>`, continuing after the highest decision id in the plan's issue (its Decisions, `gh issue view <n> --json body`), in `.agentic/prior-decisions.json`, in `decisions` and, for a slice plan, in `.agentic/epic-plan.json` `decisions`.
- `was`: the question's `OQ-<n>` id.
- `decision`: one sentence stating the choice itself.
- `rejected`: the answers not taken, one sentence each: the question's `assumed` answer, `challenge_answer` (when challenged) and `alternatives`, minus the answer the decision takes. `[]` when there were none.
- `phases`: the question's `phases`.
- `date`: today, as `YYYY-MM-DD` (`date +%F`).
- `replaces`: the question's `replaces`, copied unchanged, when it has one; leave the key out otherwise.

Then set `open_questions` to `[]`.

## 3. Update the plan's markdown

`plan.md` states no open questions or decisions: they live in `plan.json`. Replace every `OQ-<m>` reference in it, such as a phase's "[See OQ-<m>.]", with its `D-<n>`. Keep `plan.json` agreeing with `plan.md`, and in the shape [issue-plan.md](issue-plan.md) Step 5 defines for the plan's mode.

## 4. Hand off

Report each decision added, as its id, former question id and how it was settled, and plainly that nothing was implemented, committed or pushed.
