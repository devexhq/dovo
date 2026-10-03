# Dovo CLI (`dovo`) Documentation

Welcome to **Dovo CLI** (`dovo`), a CLI tool providing isolated Git worktree developer workflows and AI agent workspaces backed by a local `.dovo/` state directory.

---

## Overview

`dovo` streamlines developer workflows by creating isolated Git worktrees and managing blueprint execution cycles.

Key capabilities include:

- **Isolated Worktrees**: Safely iterate on feature code without dirtying your main working tree using `dovo worktree`.
- **Unified Blueprint Execution**: Execute cataloged blueprints via `dovo run`.
- **Durable Resumption**: Seamlessly resume paused sessions from saved run state via `dovo resume`.
- **Execution History**: Inspect and audit recorded blueprint run sessions via `dovo history`.
- **Catalog System**: Discover and manage project blueprints and steps with `dovo blueprint` and `dovo step`.

---

## Quickstart

Get started in seconds:

```bash
# Install Dovo CLI
pip install devexhq-dovo

# Initialize local Dovo configuration
dovo init

# List available catalog items
dovo blueprint list

# Create an isolated worktree environment
dovo worktree create my-feature
```

---

## Documentation Map

### 🚀 Getting Started
- **[Installation](getting-started/installation.md)**: Install with `pip`, `pipx`, or `uv`.
- **[Quickstart Tutorial](getting-started/quickstart.md)**: 5-minute tutorial to run your first blueprint in a worktree.
- **[Workspace Configuration](getting-started/workspace-config.md)**: Set up `.dovo/config.json` and project settings.

### 📖 How-To Guides
- **[Core Concepts](guides/concepts.md)**: Worktrees, blueprints, steps, and session lifecycle.
- **[Authoring Blueprints](guides/authoring-blueprints.md)**: Creating custom blueprint documents.
- **[Working with Steps](guides/working-with-steps.md)**: Command, Agent, and Script steps, shorthands, and reusable catalog steps.
- **[Parameter Inputs & Expressions](guides/passing-inputs.md)**: Declaring typed parameters, CLI flags, and `${{ inputs.* }}` interpolation.
- **[Failure Handling & Resumption](guides/failure-handling-and-resume.md)**: Retry policies, interactive prompts, durable run state, and `dovo resume`.
- **[AI Agent Providers](guides/agent-providers.md)**: Adapter selection and the current agent-step limitation.

### 📚 Reference
- **[Blueprint Schema](reference/blueprint-schema.md)**: Full generic-blueprint YAML schema reference.
- **[Step Schema](reference/step-schema.md)**: Step primitive properties, modes, and loop blocks.
- **[Inputs Schema](reference/inputs-schema.md)**: Parameter input types, aliases, and expression syntax.
- **[Assertions Schema](reference/assertions-schema.md)**: Quality assertions and verification operators.
- **[Project Config Schema](reference/config-schema.md)**: Full `.dovo/config.json` specification.

### 🍳 Recipes & Examples
- **[TDD Loop](recipes/tdd-loop.md)**: A blueprint loop that asks an agent step to fix failing tests and reruns them.
- **[AI Code Patcher](recipes/ai-code-patcher.md)**: A blueprint that plans, patches, and reviews with agent steps alongside quality gates.
- **[CI/CD Automation](recipes/ci-cd-automation.md)**: Running headless Dovo blueprints in GitHub Actions.

### 💻 CLI Reference
- **[Workspace Init (`dovo init`)](cli/init.md)**: Provision local workspace and configuration defaults.
- **[Status (`dovo status`)](cli/status.md)**: Inspect workspace health, catalog, worktrees, and recorded sessions.
- **[Config (`dovo config`)](cli/config.md)**: Display, modify, and validate project configuration.
- **[Run (`dovo run`)](cli/run.md)**: Execute a blueprint by name.
- **[Resume (`dovo resume`)](cli/resume.md)**: Resume paused blueprint sessions from saved run state.
- **[History (`dovo history`)](cli/history.md)**: List and inspect recorded blueprint runs.
- **[Logs (`dovo logs`)](cli/logs.md)**: Show a session's run timeline or a step's captured output.
- **[Diff (`dovo diff`)](cli/diff.md)**: View the diff captured for an execution session.
- **[Doctor (`dovo doctor`)](cli/doctor.md)**: Run registered workspace diagnostics.
- **[Worktree (`dovo worktree`)](cli/worktree.md)**: Git worktree isolation.
- **[Blueprint (`dovo blueprint`)](cli/blueprint.md)**: Blueprint catalog items across all tiers.
- **[Step (`dovo step`)](cli/step.md)**: Step catalog items across all tiers.
