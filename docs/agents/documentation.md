# Documentation

Guidelines for maintaining user-facing and agent documentation in Dovo CLI (companion to R17 in [AGENTS.md](../../AGENTS.md)).

## Documentation Update Gates

Update docs in the same PR **only** when a change triggers one of these gates or makes an existing doc, skill, rule, or docstring sentence false (`R17`):

- **Command added, renamed, or removed in `src/dovo/cli/cli.py`:** Update `README.md` Quick start and command surface so it matches `dovo --help` (`tests/lint/test_readme_command_parity.py` enforces this).
- **User-visible CLI behavior, arguments, or flags changed:** Update the corresponding command page under `docs/cli/<command>.md`.
- **User-visible config or blueprint schema changed:** Update the corresponding reference page under `docs/reference/` (`tests/lint/test_config_schema_parity.py` and `tests/lint/test_step_schema_parity.py` enforce schema parity).
- **Python style, package layout, or `Any`/ignore rules changed:** Update [docs/agents/code-conventions.md](code-conventions.md).
- **Test tier or harness conventions changed:** Update [docs/agents/testing.md](testing.md).
- **Existing doc or docstring sentence made false (`R17`):** Correct or delete the false sentence in the same change.

Keep docs lean: no update is better than busywork. Prefer deleting stale bullets over appending parallel prose.

## Accuracy Rules (R17)

1. **Document only shipped behavior.** Every file path, command flag, helper, or fixture named in docs, skills, or docstrings must exist in the tree today.
2. **Never duplicate source shapes in prose.** Do not copy Pydantic field tables, enum lists, or function signatures into internal docs where reading the source file already shows them unambiguously.
3. **User-facing docs (`docs/*`) speak to the user.** Write MkDocs pages in direct second-person ("you") with runnable examples and output previews.
