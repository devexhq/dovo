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

## User documentation

User docs are `docs/` outside `docs/agents/`, `README.md`, and the site metadata in `mkdocs.yml`. They are written for people who run `dovo`. Contributor material (code structure, classes, modules, tests, quality gates, rules, how claims are verified) lives only in `docs/agents/` and `.agents/skills/`. `R18` in [AGENTS.md](../../AGENTS.md) points here, and a review reports a violation as `R18` with the rule ID below.

### UDOC-001: Describe only what users see

Describe commands, flags, files in the user's project, config keys, blueprint YAML and terminal output. Never name Python classes, modules, functions, source or test paths or links, internal libraries, database tables, test suites, quality gates, or how a claim is verified.

- **Flag:** a backticked class defined in `src/dovo`, a link to `src/` or `tests/` in this repository, an internal library name, or a sentence saying a table or behaviour is pinned, enforced or checked by a test. Never flag paths in the user's own project shown in examples.
- **Good:** The `tools` field takes allow and deny lists of rules.
- **Bad:** StepDefinition fields. This table is pinned to the model by a parity test.

### UDOC-002: Headings use user-facing names

Headings and prose name things by what the user types or sees: commands, YAML keys, config keys, and codes printed in output.

- **Flag:** a heading or table caption holding a class name or a model-style CamelCase identifier.
- **Good:** `## Step fields`
- **Bad:** `## StepDefinition Fields`

### UDOC-003: Learn pages speak to the reader and start small

A page under `docs/getting-started/` or `docs/guides/` opens with one sentence saying what the reader will do, addressed to "you", in present tense and active voice, and starts from the smallest working example. Reference pages state facts in a neutral voice.

- **Flag:** a Learn page whose first sentence does not state the reader's goal, or whose first example is the full feature set.
- **Good:** In this guide you write a blueprint with one step and run it.
- **Bad:** A blueprint is a YAML file. The complete schema follows.

### UDOC-004: Callouts for surprising behaviour

Behaviour a user could get wrong (a rejected combination, an unexpected exit code, a setting a provider ignores) goes in a `!!! note`, `!!! tip` or `!!! warning` at the place it applies.

- **Flag:** surprising behaviour stated only in a table cell or a parenthetical.
- **Good:** a `!!! warning` reading "Agent steps are rejected under `--no-worktree`."
- **Bad:** `| --no-worktree | Run in place (agent steps are rejected under it). |`

### UDOC-005: Reference tables hold lookup facts

A table cell is at most 200 characters; a longer rule moves to prose below the table and the cell links to it. A Required cell reads Yes only when omitting the field is a validation error, and the Required and Default cells never contradict each other.

- **Flag:** a cell over 200 characters, or a row marked Required that also lists a default.
- **Good:** `| tools | object | No | policy default | Tool permissions for the step. See Tool Policy. |`
- **Bad:** a `tools` cell holding a 400-character paragraph.

### UDOC-006: Product and command naming

The product is written "Dovo" and the command `dovo`.

- **Flag:** "Dovo CLI" or "dovo CLI".
- **Good:** Dovo runs blueprints in isolated Git worktrees.
- **Bad:** Welcome to Dovo CLI (dovo).

### UDOC-007: Headings separate sections

No horizontal rule between sections.

- **Flag:** a line that is exactly `---` outside a fenced code block.
- **Good:** `## Step two` directly after the previous section.
- **Bad:** a `---` line followed by `## Step two`.

### UDOC-008: Concrete values match behaviour

Every concrete value (id format, path, branch name, exit code, default) is taken from the command's output or the code that produces it, never from memory.

- **Flag:** a value in the diff that differs from what the command or its code produces.
- **Good:** A run's worktree uses branch `dovo/blueprint_1a2b3c4d`, named after its session id.
- **Bad:** A run uses a `dovo/dovo_*` branch.
