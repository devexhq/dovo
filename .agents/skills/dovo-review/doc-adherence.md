# Doc adherence gates

Walk the changed-file list against this table (and [docs/agents/documentation.md](../../../docs/agents/documentation.md)). A gate fires only when the change matches its trigger or makes an existing doc/docstring sentence false (`R17`): `AGENTS.md` states that no doc update is better than busywork, so an update outside these triggers and `R17` is itself a finding.

## Required updates in the same change

| Trigger in the diff | Must be satisfied | Where |
|---|---|---|
| Command added, renamed, or removed in `src/dovo/cli/cli.py` (`COMMAND_GROUPS` / `SESSION_SHORTCUTS`) | `README.md` Quick start and command surface match `dovo --help` exactly, no documented command that does not exist, none shipped undocumented | `README.md` |
| User-visible CLI behavior or flags changed | per-command page updated | `docs/cli/` |
| User-visible config or blueprint schema changed | corresponding schema reference page updated | `docs/reference/` |
| A production symbol deleted whose only caller was a test | the test deleted in the same change, coverage expected to fall, no backfilled tests holding the percentage, and no negative existence tests (`assert not hasattr(...)`) added | `AGENTS.md`, `docs/agents/testing.md` |
| How to write Python here changed (package or module layout, model placement, `Any`/ignore rules) | conventions doc updated | `docs/agents/code-conventions.md` |
| A decision on results, domain errors, remediations or diagnostics changed, or a lint test starts enforcing one | its entry rewritten, `Enforced by` updated | `docs/agents/architecture/` |
| Test tier or harness conventions changed | testing guide updated | `docs/agents/testing.md` |
| Any change makes an existing doc, skill, rule, or docstring sentence false (`R17`) | false sentence corrected or deleted in the same change | affected doc or docstring |

## Quality of any doc change in the diff

- **No duplicated source.** A field table, model signature, or enum list hand-copied from source is a smell. In contributor docs, link to the model file and class and document only what the type hints do not show; user docs never link to source (R18). Flag new instances.
- **Unavoidable field tables need a test.** When a table is the specification for something external (a JSON Schema, a stable CLI output format), a test must fail when table and source disagree. The test names the doc it pins by a path constant; a user doc never names the test.
- **User docs (R18).** Check every line the diff adds or changes under `docs/` outside `docs/agents/`, in `README.md` and in `mkdocs.yml` against `UDOC-001` to `UDOC-008` in [docs/agents/documentation.md](../../../docs/agents/documentation.md#user-documentation). Report each violation with rule `R18`, the UDOC ID and the `path:line`. Do not report lines the diff does not touch.
- **Prefer deletion over accretion.** A stale bullet replaced is better than a parallel truth appended. Flag a doc that now states two contradictory things.

## Process directives

These are `AGENTS.md` directives about how the work was done. Report violations as findings, but do not block otherwise-correct code on them alone.

- **Plan before code.** A change that adds surface needed a plan per `docs/agents/planning.md`. Skippable only for a single-file change adding no new surface.
- **Size against the plan.** A diff that runs well past the plan's summed size estimates should have been planned differently. `/dovo-code` lands every phase in one PR, so a diff that spans several phases is expected.
- **Gates before commit.** `dovo run quality` had to pass before committing. This skill does not run them (`/dovo-code` owns the gate); ask whether they were run and report the answer as reported, never as verified.
