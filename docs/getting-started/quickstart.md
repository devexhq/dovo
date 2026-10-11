# Quickstart Tutorial

This tutorial initializes a workspace, creates a blueprint, and runs it in an isolated worktree.

---

## 1. Initialize Your Workspace

Navigate to your Git repository root and run `dovo init`:

```bash
dovo init
```

This provisions the `.dovo/` state directory:

```text
.dovo/
├── .gitignore          # Local ignore rules
├── .meta/              # Local catalog metadata
├── config.json         # Project settings
├── project.json        # Project identity
└── catalog/            # Project blueprints and steps
    ├── blueprints/
    └── steps/
```

Verify your workspace health:

```bash
dovo status
```

---

## 2. Discover Built-in Blueprints

List the blueprints in the catalog, including the built-in ones:

```bash
dovo blueprint list
```

Show a built-in step:

```bash
dovo step show dovo/git-sync-base
```

---

## 3. Create a Custom Blueprint

Create a new blueprint called `lint-and-format`:

```bash
dovo blueprint create --name lint-and-format
```

Open `.dovo/catalog/blueprints/lint-and-format.yml` in your editor and configure its steps:

```yaml
name: lint-and-format
description: Run code linters and formatters in an isolated environment
use_worktree: true

steps:
  - id: check-lint
    name: Lint code
    run: ruff check .
    on_failure: abort

  - id: check-format
    name: Format check
    run: ruff format --check .
    on_failure: abort
```

---

## 4. Run the Blueprint in an Isolated Worktree

Execute your newly created blueprint:

```bash
dovo run lint-and-format
```

### What happens

1. `dovo` creates an ephemeral Git worktree on a temporary `dovo/dovo_*` branch.
2. Steps run in order inside the worktree.
3. If all steps succeed, the worktree is removed.
4. The result, step output and duration are recorded for the session.

---

## 5. Inspect Execution History

List past runs:

```bash
dovo history list
```

Show one run's details:

```bash
dovo history show <session-id>
```

---

## Next Steps

- [Core Concepts](../guides/concepts.md)
- [Authoring Blueprints](../guides/authoring-blueprints.md)
- [AI Agent Providers](../guides/agent-providers.md)
