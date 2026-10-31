# plan-walkthrough

Explain a plan written by `issue-plan` to the human who approves it. The plan is written for the coding and review agents: it is long, cites code by `path:line` and is organised by phase and requirement. The walkthrough is written for a person: it is short, organised by what a Dovo user sees, and ends with the consequences the person should weigh before approving. It does not restate the plan's phases or tests; the person can open the plan for those.

Output goes to `.agentic/plan-walkthrough.md`, at most 60 lines. The blueprint shows it at the plan approval prompt.

## Hard boundaries

- **Never edit the plan, `plan.json`, the phase files, the issue, `src/`, `tests/` or `docs/`, and never commit or push.** Write only `.agentic/plan-walkthrough.md`.
- **Never run tests or tooling.** Read-only `git`, read-only `gh`, `rg` and file reads are enough.
- **Re-read from disk.** Every file you describe is read fresh in this session, never recalled.
- **Describe only what the plan does.** The walkthrough explains the plan; it does not review or challenge it. A trap is a consequence of the plan as written, not a defect report.

## 1. Resolve the input

The plan is `<plan-path>` when given, otherwise `.agentic/plan.md`. If it is missing, stop and say so. Read it in full, every file it links to, and `plan.json` beside it, whose `decisions` hold every decision the plan is written against. In slice mode, also read the breakdown: `.agentic/phases/index.json` and every phase file it lists, the only files each phase's code agent reads. When `plan.json`'s `issue` is a number, read that issue with `gh issue view <n> --json number,title,body`. When it is `null`, the plan was written from a plain-language request: work from the plan, its `## Requirements` list and its linked files, and run no issue lookup.

## 2. Ground "today" in the code

For every behaviour the walkthrough says changes, open the code the plan cites and confirm what a user sees today: the message text, where it appears, the JSON keys, the exit code. The plan's description of current behaviour is a claim; the code is the evidence. When the code disagrees with the plan, describe the code, and say so in one line under **Traps** so the approver knows the plan rests on a wrong premise.

## 3. Write the walkthrough

Write `.agentic/plan-walkthrough.md`, overwriting any earlier one, in this structure and order:

```markdown
# Walkthrough: <#n, when there is an issue,> <short title>

## In one paragraph

<The problem today, the change, and what is deliberately left for later, in plain words. No file paths.>

## What a user sees, before and after

| Situation | Today | After this PR |
|---|---|---|
| <command and condition, e.g. `dovo blueprint create` fails unexpectedly (terminal)> | <what the user sees now> | <what they will see> |

## Slices

- **<id>**: <what it delivers, by its outcomes>. <what it waits on, if anything>.

## Decisions to weigh

- **<decision>.** <the choice, the rejected alternative, and who settled it, in one line>.

## Breakdown

<One line: the phase files carry the plan faithfully, or what a reader of only the plan would miss.>

## Traps that materially affect a Dovo user

1. **<the consequence, as a user would notice it>.** <who hits it, when, and what they lose or must do differently>. <what the approver could ask for instead, or the issue that resolves it>.
```

Write for a person who knows Dovo as a user and a contributor but has not read the plan:

- **Before and after.** One row per user-visible situation the plan changes: a command and condition, in terminal and `--format json` separately when they differ. Bold the cells where a user loses something they have today. For a change with no user-visible effect, write one row saying so, and what the change is for.
- **Slices.** Only in issue mode, when the plan splits the issue into two or more slices: one line per slice, in the order they land. Leave the section out otherwise.
- **Decisions.** Only those a human might disagree with: open questions answered by the human or accepted with their assumed answer, departures from the issue, and trade-offs. One line each. Do not cite `OQ-<n>`, review or challenge ids; state the decision itself.
- **Breakdown.** In slice mode only: a trap or decision the plan ties to a phase that its file leaves out, or an action whose `do` asks for more or less than the phase section. Leave the section out in issue mode.
- **Traps.** At most 5: consequences of the plan as written that change a Dovo user's experience, such as output that disappears, moves or changes shape; a `--format json` consumer that loses information or must read a new field; a temporary regression and the issue that ends it; persisted data written before the change; a failure that becomes silent. Rank them by how many users notice and how much they lose. When there are none, keep the heading with the line `none`.

Every claim about today's behaviour comes from the code (step 2). Every claim about the change comes from the plan or the issue. When a trap is your own reading of the plan rather than something it states, end the trap with `(inferred from the plan)`.

## 4. Hand off

Report the walkthrough path, its line count and the number of traps. Do not edit the plan: the human reads the walkthrough at the plan approval prompt, and the full plan stays in the `plan` artifact.
