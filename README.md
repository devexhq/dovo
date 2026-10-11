# Dovo CLI (`dovo`)

[![CI](https://github.com/devexhq/dovo/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/devexhq/dovo/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/devexhq/dovo.svg)](https://github.com/devexhq/dovo/blob/main/LICENSE)
[![Issues](https://img.shields.io/github/issues/devexhq/dovo.svg)](https://github.com/devexhq/dovo/issues)
[![Last commit](https://img.shields.io/github/last-commit/devexhq/dovo.svg)](https://github.com/devexhq/dovo/commits/main)
[![PyPI version](https://img.shields.io/pypi/v/devexhq-dovo.svg)](https://pypi.org/project/devexhq-dovo/)
[![Python versions](https://img.shields.io/pypi/pyversions/devexhq-dovo.svg)](https://pypi.org/project/devexhq-dovo/)
[![Stars](https://img.shields.io/github/stars/devexhq/dovo.svg?style=social)](https://github.com/devexhq/dovo/stargazers)

Isolated git worktree developer workflows and autonomous AI agent workspaces.

Dovo helps humans and agents build, test, and remediate in parallel without disturbing your active local branch.

## Installation

```bash
pip install devexhq-dovo
```

## Requirements

- Python 3.13+
- Git
- For agent steps (default provider `copilot`):
  - Copilot: GitHub CLI (`gh`) on `PATH` + `GH_TOKEN` or `GITHUB_TOKEN`

## Quick start

```bash
dovo init
dovo status
dovo config validate
dovo blueprint list
dovo run fix-tests
dovo history
```

## Current command surface

### Top-level

- `dovo init`
- `dovo status`
- `dovo diff [session-id]` — view syntax-highlighted session diff
- `dovo doctor` — run diagnostic checks and print a workspace health report
- `dovo run <blueprint>` — execute a task or workflow blueprint
- `dovo resume <session-id>` — resume a paused run
- `dovo history` / `dovo history list` — list past sessions
- `dovo history show <session-id> [--logs]` — inspect a past session
- `dovo logs <session-id>` — show a session's timeline or a step's captured output (`--step`, `--attempt`, `--stream`, `--tail`)

### Config

- `dovo config show`
- `dovo config set <key> <value>`
- `dovo config unset <key>`
- `dovo config validate`

### Blueprints

- `dovo blueprint list` / `dovo blueprint ls` — list blueprint catalog items across all tiers
- `dovo blueprint create --name <name>` — create a new repo-tier blueprint
- `dovo blueprint show <sha-or-name>`
- `dovo blueprint delete <sha-or-name>`
- `dovo blueprint validate <target>` — validate a blueprint definition without executing it

### Steps

- `dovo step list` / `dovo step ls` — list step catalog items across all tiers
- `dovo step create --name <name>` — create a new repo-tier step
- `dovo step show <sha-or-name>`
- `dovo step delete <sha-or-name>`
- `dovo step validate <target>` — validate a step definition without executing it

### Worktree

- `dovo worktree create`
- `dovo worktree list`
- `dovo worktree show <worktree-id>`
- `dovo worktree prune` — safely prune stale worktrees, orphaned directories, and temporary branches
- `dovo worktree delete <worktree-id>`
- `dovo worktree apply <worktree-id>` — apply worktree changes back to workspace
- `dovo worktree diff <worktree-id>` — inspect differences from base commit

### Artifacts

- `dovo artifacts list [--session <session-id>]` — list published session artifacts
- `dovo artifacts download <session-id> <name> --dest <path>` — checksum-verify and extract a published artifact bundle
- `dovo artifacts prune [--dry-run] [--force]` — delete expired artifact bundles

## Agent providers for workflow runs

Workflow definitions support one provider:

- `copilot`

`copilot` runs as a direct-mutation provider with shared safety gates before patches are accepted. Other providers are delayed beyond v1.

## Reserved `.dovo` paths

Dovo reserves these paths under the repository-local `.dovo/` directory:

- `.dovo/config.json`
- `.dovo/project.json`
- `.dovo/catalog/workflows/*.yml`
- `.dovo/catalog/tasks/*.yml`
- `.dovo/worktrees/`
- `.dovo/dovo.lock`
- `<worktree>/.dovo/run`, a symlink to the run's session directory that `dovo run` creates in each new run worktree

Runtime session, artifact, log, and temporary data live outside the repository at:

- `DOVO_HOME/storage/projects/<project-id>/sessions/` and `artifacts/` (`~/.dovo` when `DOVO_HOME` is unset)

## Development

```bash
uv sync --all-extras
uv run dovo run tests    # scoped: --path tests/core/, --fast-fail, --coverage
uv run dovo run quality  # full quality gate (format check, lint, typecheck, docs, complexity, tests)
```

## Documentation

- Schemas and entities: [docs/agents/schemas.md](docs/agents/schemas.md)
- Architecture: [docs/agents/architecture.md](docs/agents/architecture.md)

## Project status

This README reflects the currently implemented surface in `main`.
Additional commands may still be in progress.

## License

Distributed under the MIT License. See [`LICENSE`](LICENSE) for details.

## Links

- Website: [devexhq.github.io/dovo](https://devexhq.github.io/dovo)
- Repository: [github.com/devexhq/dovo](https://github.com/devexhq/dovo)
