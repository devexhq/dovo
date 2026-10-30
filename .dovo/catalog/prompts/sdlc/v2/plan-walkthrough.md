# plan-walkthrough

Explain a plan written by `issue-plan` to the human who approves it. The plan is written for the coding and review agents: it is long, cites code by `path:line` and is organised by phase and requirement. The walkthrough is written for a person: it is short, organised by what a Dovo user sees, and ends with the consequences the person should weigh before approving.

Output goes to `.agentic/plan-walkthrough.md`. The blueprint shows it at the plan approval prompt, which cuts a file at 200 lines, so the whole walkthrough must fit in 200 lines; aim for about 100.

## Hard boundaries

- **Never edit the plan, `plan.json`, the issue, `src/`, `tests/` or `docs/`, and never commit or push.** Write only `.agentic/plan-walkthrough.md`.
- **Never run tests or tooling.** Read-only `git`, read-only `gh`, `rg` and file reads are enough.
- **Re-read from disk.** Every file you describe is read fresh in this session, never recalled.
- **Describe only what the plan does.** The walkthrough explains the plan; it does not review or challenge it. A trap is a consequence of the plan as written, not a defect report.

## 1. Resolve the input

The plan is `<plan-path>` when given, otherwise `.agentic/plan.md`. If it is missing, stop and say so. Read it in full, every file it links to, and `plan.json` beside it. When `plan.json`'s `issue` is a number, read that issue with `gh issue view <n> --json number,title,body`. When it is `null`, the plan was written from a plain-language request: work from the plan, its `## Requirements` list and its linked files, and run no issue lookup.

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

## How the agent will build it

<One numbered item per phase: its name, its estimated changed lines, and in two or three sentences what it changes and what becomes visible to a user after it. State the total and that every phase lands in one PR.>

## Decisions already settled

- **<decision>.** <the choice and why, in one sentence>. <the rejected alternative, in a clause>.

## What to check in the PR

- <a concrete thing a human reviewer should look for in the diff or the tests>

## Traps that materially affect a Dovo user

1. **<the consequence, as a user would notice it>.** <who hits it, when, and what they lose or must do differently>. <what the approver could ask for instead, or the issue that resolves it>.
```

Write for a person who knows Dovo as a user and a contributor but has not read the plan:

- **Before and after.** One row per user-visible situation the plan changes: a command and condition, in terminal and `--format json` separately when they differ. Bold the cells where a user loses something they have today. Add a row for what stays unchanged when a reader would reasonably expect it to change, such as related commands the issue does not change. For a change with no user-visible effect, write one row saying so, and what the change is for.
- **Phases.** Plain outcomes, not file lists. Name a symbol or file only when a reviewer needs it to find the change.
- **Decisions.** Only those a human might disagree with: open questions answered by the human or accepted with their assumed answer, departures from the issue, and trade-offs. Drop decisions only an implementer would care about. Do not cite `OQ-<n>`, review or challenge ids; state the decision itself.
- **What to check.** At most five items, each something a person can verify in minutes: a test that pins a contract, a search that must come back empty, a kind of edit that would weaken an assertion.
- **Traps.** Consequences of the plan as written that change a Dovo user's experience: output that disappears, moves or changes shape; a `--format json` consumer that loses information or must read a new field; behaviour that differs between converted and unconverted commands while follow-up issues are open; a temporary regression and the issue that ends it; persisted data written before the change; a failure that becomes silent. Rank them by how many users notice and how much they lose. Close with any `Not changed, but worth knowing` item that a user might assume changed. When there are none, keep the heading with the line `none`.

Every claim about today's behaviour comes from the code (step 2). Every claim about the change comes from the plan or the issue. When a trap is your own reading of the plan rather than something it states, end the trap with `(inferred from the plan)`.

## 4. Hand off

Report the walkthrough path, its line count and the number of traps. Do not edit the plan: the human reads the walkthrough at the plan approval prompt, and the full plan stays in the `plan` artifact.
