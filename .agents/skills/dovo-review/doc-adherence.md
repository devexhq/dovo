# Doc adherence gates

Walk the changed-file list against this table. A gate fires only when the change matches its trigger: `AGENTS.md` states that no doc update is better than busywork, so an update outside these gates is itself a finding.

Read the doc named by any gate that fires before judging whether it was satisfied.

## Required updates in the same change

| Trigger in the diff | Must be satisfied | Where |
|---|---|---|
| Package layout, domain ownership, or import boundaries changed | `architecture.md` *structure* sections updated (layers tree, ownership, boundaries) and nothing else appended there | `docs/agents/architecture.md` |
| Command added, renamed, or removed in `src/dovo/cli/cli.py` (`add_typer` / `register_*`) | `README.md` Quick start and command surface match `dovo --help` exactly, no documented command that does not exist, none shipped undocumented | `README.md` |
| User-visible CLI behavior or flags changed | per-command page updated (not `architecture.md`) | `docs/cli/` |
| A cross-cutting contract changed (config merge rules, path ownership, persistence authority, secrets handling) | schema doc updated | `docs/agents/schemas.md` |
| `core/db/` schema, table, or migration touched | migration hygiene checklist followed; a new table or column has a real caller in the same change, or an explicit note on why it lands ahead of one | `docs/agents/ci-and-tooling.md#migration-hygiene` |
| New agent provider added | provider procedure followed and its setup failure modes documented | `architecture.md#adding-a-new-agent-provider` plus `docs/agents/troubleshooting.md` |
| A package or subsystem removed | removal procedure followed | `docs/agents/ci-and-tooling.md#removing-dead-code` |
| A production symbol deleted whose only caller was a test | the test deleted in the same change, coverage expected to fall, no backfilled tests holding the percentage, and no negative existence tests (`assert not hasattr(...)`) added | `AGENTS.md`, `docs/agents/testing.md` |
| How to write Python here changed (placement, `Result`/`Outcome`, error or DRY rules) | conventions doc updated | `docs/agents/code-conventions.md` |
| Commit or PR opened as part of the change | commit and PR conventions followed | `docs/agents/git-and-pr-conventions.md` |
| A GitHub issue created or updated | issue structure, tone, and required sections followed | `docs/agents/github-issues.md` |

A pure refactor that does not change public layout or ownership needs no `architecture.md` diff.

## Quality of any doc change in the diff

- **No duplicated source.** A field table, model signature, or enum list hand-copied from source is a smell: it should link to the model file and class and document only what the type hints do not show (validators, resolution order, cross-field invariants, why a field exists). Flag new instances.
- **Unavoidable field tables need a test.** When a table is the specification for something external (a JSON Schema, a stable CLI output format), a test must fail when table and source disagree. A doc claim with no test behind it will eventually be wrong.
- **Prefer deletion over accretion.** A stale bullet replaced is better than a parallel truth appended. Flag a doc that now states two contradictory things.
- **Right doc for the content.** Feature behavior narrative in `architecture.md`, or user-facing CLI behavior anywhere but `docs/cli/`, is misplaced.
- **Terminology.** Task, workflow, blueprint, step, run, session, worktree, and checkpoint each mean something specific; check new prose against the Terminology table in `docs/agents/architecture.md`.

## Process directives

These are `AGENTS.md` directives about how the work was done. Report violations as findings, but do not block otherwise-correct code on them alone.

- **Governing directive stated.** Before executing commands or editing files, the author was to state the directive or doc governing the action and the target scope.
- **Plan before code.** Implementing a GitHub issue required a plan per `docs/agents/planning.md` (contract extraction, grounding in the current tree, an artifact inventory with code samples). Skippable only for a single-file change adding no new surface.
- **Gates before commit.** `inv test -c`, `ruff format`, `ruff check`, `basedpyright src --level error`, and `inv complexity --paths <changed> --plain --failed` all had to pass before committing. This skill does not run them (`/dovo-code` owns the gate); ask whether they were run and report the answer as reported, never as verified.
