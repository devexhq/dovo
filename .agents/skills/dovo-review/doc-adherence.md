# Doc adherence gates

Walk the changed-file list against this table. A gate fires only when the change matches its trigger: `AGENTS.md` states that no doc update is better than busywork, so an update outside these gates is itself a finding.

## Required updates in the same change

| Trigger in the diff | Must be satisfied | Where |
|---|---|---|
| Command added, renamed, or removed in `src/dovo/cli/cli.py` (`add_typer` / `register_*`) | `README.md` Quick start and command surface match `dovo --help` exactly, no documented command that does not exist, none shipped undocumented | `README.md` |
| User-visible CLI behavior or flags changed | per-command page updated | `docs/cli/` |
| A production symbol deleted whose only caller was a test | the test deleted in the same change, coverage expected to fall, no backfilled tests holding the percentage, and no negative existence tests (`assert not hasattr(...)`) added | `AGENTS.md`, `docs/agents/testing.md` |
| How to write Python here changed (placement, `Result`/`Outcome`, error or `Any`/ignore rules) | conventions doc updated | `docs/agents/code-conventions.md` |

## Quality of any doc change in the diff

- **No duplicated source.** A field table, model signature, or enum list hand-copied from source is a smell: link to the model file and class and document only what the type hints do not show. Flag new instances.
- **Unavoidable field tables need a test.** When a table is the specification for something external (a JSON Schema, a stable CLI output format), a test must fail when table and source disagree.
- **Prefer deletion over accretion.** A stale bullet replaced is better than a parallel truth appended. Flag a doc that now states two contradictory things.

## Process directives

These are `AGENTS.md` directives about how the work was done. Report violations as findings, but do not block otherwise-correct code on them alone.

- **Plan before code.** A change that adds surface needed a plan per `docs/agents/planning.md`. Skippable only for a single-file change adding no new surface.
- **One phase per PR.** A diff that spans several plan phases, or runs well past the plan's size estimate, should have been split.
- **Gates before commit.** `dovo run quality` had to pass before committing. This skill does not run them (`/dovo-code` owns the gate); ask whether they were run and report the answer as reported, never as verified.
