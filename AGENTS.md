# AGENTS.md

`Worktree` (`wt`) is a Typer-based CLI providing isolated Git worktree
developer workflows and AI agent workspaces, backed by a local `.worktree/` state
directory.

## Domain rules (RULES.md)

Do not read broad always-on documentation before starting a task. Instead, read and apply the domain-specific `RULES.md` corresponding to the section of the codebase being modified:

| When editing files under | Read and apply |
|---|---|
| `src/worktree/cli/` | [src/worktree/cli/docs/RULES.md](src/worktree/cli/docs/RULES.md) |
| `src/worktree/common/` | [src/worktree/common/docs/RULES.md](src/worktree/common/docs/RULES.md) |
| `src/worktree/core/` | [src/worktree/core/docs/RULES.md](src/worktree/core/docs/RULES.md) |
| `src/worktree/engine/` | [src/worktree/engine/docs/RULES.md](src/worktree/engine/docs/RULES.md) |
| `tests/` | [tests/docs/RULES.md](tests/docs/RULES.md) |

## Agentic process

Before executing commands or editing files, state:
    1. The specific directive/doc governing this action.
    2. The target scope (e.g. specific test package or module).

When implementing a GitHub issue, **plan before writing code**: follow
[docs/agents/planning.md](docs/agents/planning.md) to extract the issue's
contract, ground it in the current tree, and enumerate every artifact (DTOs,
services, facade methods, commands, subcommands, formatters, schemas, tests,
docs) into a plan with code samples. Skip it only for a single-file change that
adds no new surface.

At the end of a unit of work, on top of providing a summary of changes and
quality gate results, provide a commit message following
[docs/agents/git-and-pr-conventions.md](docs/agents/git-and-pr-conventions.md).

## Essential commands

```bash
uv sync --all-extras            # install dependencies with uv (or uv pip install -e .[dev])
uv run inv test                 # run tests (python -m pytest -n auto tests/ -q)
uv run ruff check .             # lint
uv run ruff format .            # format
uv run basedpyright src tests   # typecheck package and tests (errors must be 0)
inv complexity --paths <changed-file1>,<changed-file2> --plain   # complexity gate for changed files
uv run python scripts/compile_rules.py   # recompile RULES.md after editing rules_spec.yaml
```

## Quality gates

Before committing, all 5 quality gates must pass (see [docs/agents/ci-and-tooling.md](docs/agents/ci-and-tooling.md) and [docs/agents/testing.md](docs/agents/testing.md)):
  - `uv run inv test -c` (coverage, **≥ 80%** via `fail_under` in `pyproject.toml`)
  - `uv run ruff format`
  - `uv run ruff check`
  - `uv run basedpyright src tests --level error`
  - `uv run inv complexity --paths <changed-file1>,<changed-file2> --plain --failed` (no touched function may exceed complexity 10)

## Documentation policy

Documentation update gates, accuracy rules, and user-facing doc sync policies are defined in [docs/agents/documentation.md](docs/agents/documentation.md).

## Docs

| Doc | When to use |
|-----|-------------|
| [docs/agents/architecture.md](docs/agents/architecture.md) | Module layout, domain ownership, import boundaries, `.worktree/` layout (structure only) |
| [docs/agents/code-conventions.md](docs/agents/code-conventions.md) | Python style, models placement, Result/Outcome, writes, console output, backwards compatibility |
| [docs/agents/documentation.md](docs/agents/documentation.md) | Documentation update gates, accuracy rules, and user-facing doc sync |
| [docs/agents/planning.md](docs/agents/planning.md) | Planning an issue before implementation (artifact inventory, code samples, plan template) |
| [docs/agents/testing.md](docs/agents/testing.md) | Adding or running tests (execution tiers, harness matchers, contracts) |
| [docs/agents/schemas.md](docs/agents/schemas.md) | Entity shapes (exceptions, DTOs, facades, commands), config & blueprint schemas |
| [docs/agents/glossary.md](docs/agents/glossary.md) | Disambiguating task / workflow / blueprint / step / run / session / sandbox / checkpoint |
| [docs/agents/troubleshooting.md](docs/agents/troubleshooting.md) | Diagnosing agent-provider setup failures (missing keys, missing CLIs, timeouts) |
| [docs/agents/git-and-pr-conventions.md](docs/agents/git-and-pr-conventions.md) | Committing changes or opening a PR |
| [docs/agents/github-issues.md](docs/agents/github-issues.md) | Creating or updating GitHub issues (structure, tone, required sections) |
| [docs/agents/ci-and-tooling.md](docs/agents/ci-and-tooling.md) | Understanding lint/CI requirements or release versioning |
| [docs/agents/rules_spec.yaml](docs/agents/rules_spec.yaml) | Source specification for domain rules and invariants (recompile with `compile_rules.py` after changes) |
| [docs/cli/](docs/cli/) | Per-command reference (`wt catalog`, `wt run`, `wt config`, etc.) for user-facing behavior and flags |
