# Documentation

Guidelines for maintaining and updating documentation in Dovo CLI.

## Documentation update gates

Update docs in the same PR only when the change matches one of these gates:

- **Package layout / ownership / import boundaries**: update
  [docs/agents/architecture.md](architecture.md) *structure*
  sections only (layers tree, domain ownership, boundaries). Do **not** append
  feature behavior essays there.
- **How to write Python in this repo** (models placement, Result/Outcome, DRY,
  errors): update [docs/agents/code-conventions.md](code-conventions.md).
- **User-visible CLI behavior**: update [docs/cli/](../cli/) (not architecture).
- **config.json / blueprint YAML fields / entity shapes**: update
  [docs/agents/schemas.md](schemas.md).
- **`core/db/` schema, tables, or migrations**: follow the migration hygiene
  checklist in
  [docs/agents/ci-and-tooling.md](ci-and-tooling.md#database-migration-hygiene)
  before merging — a new table/column needs a real caller in the same PR or an
  explicit note on why it's landing ahead of one.
- **Adding an agent provider**: follow
  [docs/agents/architecture.md](architecture.md#adding-a-new-agent-provider)
  and add its setup failure modes to
  [docs/agents/troubleshooting.md](troubleshooting.md).
- **Removing a package/subsystem**: follow
  [docs/agents/ci-and-tooling.md](ci-and-tooling.md#dead-code-removal).
- **Deleting a production symbol whose only caller was a test**: delete the test
  in the same PR and expect coverage to fall. Do not backfill tests to hold the
  percentage.
- **Architectural invariants, domain rules, or planner constraints**: update
  [`docs/agents/rules_spec.yaml`](rules_spec.yaml) only if needed.
  Avoid useless churn: only relevant changes, deletions, and additions are
  acceptable. When updating `rules_spec.yaml`, always run
  `uv run python scripts/compile_rules.py` after to regenerate the compiled rule
  artifacts (`**/docs/RULES.md` and `docs/agents/PLANNER_RULES.md`).

Keep docs lean: no update is better than busywork. Prefer **deleting stale
bullets** over appending a parallel truth. Pure refactors that do not change
public layout or ownership need no architecture.md diff.

## Keeping docs accurate

Docs go stale in a specific, avoidable way: a field table, model signature, or enum
list gets hand-copied from source, then the source changes and the doc doesn't
(there's no gate that would catch it — it's not covered by `ruff`/`basedpyright`/
tests). This has actually happened more than once (see
[schemas.md](schemas.md)'s history). Two rules
that prevent it, in priority order:

1. **Don't duplicate what a `Read` of the source already gives you unambiguously.**
   If a doc's job is to describe a Pydantic model's field names, types, and
   defaults, that's a smell — link to the model file/class instead and document
   only the behavior that *isn't* visible from the type hints (validators,
   resolution order, cross-field invariants, why a field exists). A doc earns its
   keep by capturing things assembled across multiple files, not by re-typing one
   file's contents in prose.
2. **When a field table genuinely can't be avoided** (it's the specification for
   something external, like a JSON Schema or a CLI's stable output format), add or
   extend a test that fails when the table and the source disagree, rather than
   trusting a future editor to remember to update both. A doc claim with no test
   behind it is a claim that will eventually be wrong.

If you're about to make an implementation decision based on a doc's stated field
list or model shape, and that doc doesn't point you at the source file, spot-check
the source before trusting it — the whole point of consulting the doc instead of
the source is trust, and that only holds if the doc holds up.

## Keeping user-facing docs in sync

`README.md`'s command surface must match `dovo --help` (i.e. the
`add_typer`/`register_*` calls in
[src/dovo/cli/cli.py](../../src/dovo/cli/cli.py)) exactly. When a command is added, renamed, or removed in
`cli.py`, update `README.md` in the same PR.
