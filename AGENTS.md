# AGENTS.md

`Dovo` (`dovo`) is a Typer-based CLI providing isolated Git worktree
developer workflows and AI agent workspaces, backed by a local `.dovo/` state
directory.

## Domain rules (RULES.md)

Do not read broad always-on documentation before starting a task. Instead, read and apply the domain-specific `RULES.md` corresponding to the section of the codebase being modified:

| When editing files under | Read and apply |
|---|---|
| `src/dovo/cli/` | [src/dovo/cli/docs/RULES.md](src/dovo/cli/docs/RULES.md) |
| `src/dovo/common/` | [src/dovo/common/docs/RULES.md](src/dovo/common/docs/RULES.md) |
| `src/dovo/core/` | [src/dovo/core/docs/RULES.md](src/dovo/core/docs/RULES.md) |
| `src/dovo/engine/` | [src/dovo/engine/docs/RULES.md](src/dovo/engine/docs/RULES.md) |
| `tests/` | [tests/docs/RULES.md](tests/docs/RULES.md) |

## Agentic process

Before executing commands or editing files, state:
    1. The specific directive/doc governing this action.
    2. The target scope (e.g. specific test package or module).

When implementing a GitHub issue, **plan before writing code**: follow
[docs/agents/planning.md](docs/agents/planning.md) to extract the issue's
contract, ground it in the current tree, and enumerate every artifact (DTOs,
services, entrypoint methods, commands, subcommands, formatters, schemas, tests,
docs) into a plan with code samples. Skip it only for a single-file change that
adds no new surface.

At the end of a unit of work, on top of providing a summary of changes and
quality gate results, provide a commit message following
[docs/agents/git-and-pr-conventions.md](docs/agents/git-and-pr-conventions.md).

## Essential commands

```bash
uv sync --all-extras            # install dependencies with uv (or uv pip install -e .[dev])
uv run dovo run tests --path tests/core/   # scoped tests while iterating (also --fast-fail, --coverage)
uv run dovo run quality         # all quality gates against the working tree
uv run ruff check .             # lint
uv run ruff format .            # format
uv run basedpyright src tests   # typecheck package and tests (errors must be 0)
uv run python scripts/compile_rules.py   # recompile RULES.md after editing rules_spec.yaml
```

## Quality gates

Before committing, `uv run dovo run quality` must pass (see [docs/agents/ci-and-tooling.md](docs/agents/ci-and-tooling.md) and [docs/agents/testing.md](docs/agents/testing.md)). It runs, in order, and stops at the first failure:
  - `ruff format --check` (it does not rewrite files; run `uv run ruff format .` first)
  - `ruff check`
  - `basedpyright src tests --level error`
  - `mkdocs build --strict`
  - `complexipy` on the files changed against `origin/main` (no touched function may exceed complexity 10)
  - the full test suite with coverage (**≥ 80%** via `fail_under` in `pyproject.toml`)

CI runs the same blueprint. For quick feedback while iterating, use `uv run dovo run tests` with `--path`; it never replaces the full gate.

## Documentation policy

Documentation update gates, accuracy rules, and user-facing doc sync policies are defined in [docs/agents/documentation.md](docs/agents/documentation.md).

## Docs

| Doc | When to use |
|-----|-------------|
| [docs/agents/architecture.md](docs/agents/architecture.md) | Module layout, import boundaries, terminology (task / workflow / blueprint / step / run / session / worktree / checkpoint), `.dovo/` layout |
| [docs/agents/code-conventions.md](docs/agents/code-conventions.md) | Python style, models placement, Result/Outcome, writes, console output, backwards compatibility |
| [docs/agents/documentation.md](docs/agents/documentation.md) | Documentation update gates, accuracy rules, and user-facing doc sync |
| [docs/agents/planning.md](docs/agents/planning.md) | Planning an issue before implementation (artifact inventory, code samples, plan template) |
| [docs/agents/testing.md](docs/agents/testing.md) | Adding or running tests (execution tiers, harness matchers, contracts) |
| [docs/agents/schemas.md](docs/agents/schemas.md) | Cross-cutting contracts (config merge, paths, persistence authority, secrets) and where each entity's source lives |
| [docs/agents/troubleshooting.md](docs/agents/troubleshooting.md) | Diagnosing agent-provider setup failures (missing keys, missing CLIs, timeouts) |
| [docs/agents/git-and-pr-conventions.md](docs/agents/git-and-pr-conventions.md) | Committing changes or opening a PR |
| [docs/agents/github-issues.md](docs/agents/github-issues.md) | Creating or updating GitHub issues (structure, tone, required sections) |
| [docs/agents/ci-and-tooling.md](docs/agents/ci-and-tooling.md) | Understanding lint/CI requirements or release versioning |
| [docs/agents/rules_spec.yaml](docs/agents/rules_spec.yaml) | Source specification for domain rules and invariants (recompile with `compile_rules.py` after changes) |
| [docs/cli/](docs/cli/) | Per-command reference (`dovo catalog`, `dovo run`, `dovo config`, etc.) for user-facing behavior and flags |
