# Worktree CLI (`wt`) Documentation

Welcome to **Worktree CLI** (`wt`), a CLI tool providing isolated Git worktree developer workflows and AI agent workspaces backed by a local `.worktree/` state directory.

---

## Overview

`wt` streamlines developer workflows by creating isolated Git worktrees and managing blueprint execution cycles.

Key capabilities include:

- **Isolated Sandboxes**: Safely iterate on feature code without dirtying your main working tree using `wt sandbox`.
- **Unified Blueprint Execution**: Execute cataloged blueprints via `wt run`.
- **Durable Resumption**: Seamlessly resume paused sessions from saved run state via `wt resume`.
- **Execution History**: Inspect and audit recorded blueprint run sessions via `wt history`.
- **Catalog System**: Discover and manage project blueprints and steps with `wt blueprint` and `wt step`.

---

## Quickstart

Get started in seconds:

```bash
# Install Worktree CLI
pip install worktree-cli

# Initialize local worktree configuration
wt init

# List available catalog items
wt blueprint list

# Create an isolated sandbox environment
wt sandbox create my-feature
```

---

## Documentation Map

### 🚀 Getting Started
- **[Installation](getting-started/installation.md)**: Install with `pip`, `pipx`, or `uv`.
- **[Quickstart Tutorial](getting-started/quickstart.md)**: 5-minute tutorial to run your first blueprint in a sandbox.
- **[Workspace Configuration](getting-started/workspace-config.md)**: Set up `.worktree/config.json` and project settings.

### 📖 How-To Guides
- **[Core Concepts](guides/concepts.md)**: Sandboxes, blueprints, steps, and session lifecycle.
- **[Authoring Blueprints](guides/authoring-blueprints.md)**: Creating custom blueprint documents.
- **[Working with Steps](guides/working-with-steps.md)**: Command, Agent, and Script steps, shorthands, and reusable catalog steps.
- **[Parameter Inputs & Expressions](guides/passing-inputs.md)**: Declaring typed parameters, CLI flags, and `${{ inputs.* }}` interpolation.
- **[Failure Handling & Resumption](guides/failure-handling-and-resume.md)**: Retry policies, interactive prompts, durable run state, and `wt resume`.
- **[AI Agent Providers](guides/agent-providers.md)**: Adapter selection and the current agent-step limitation.

### 📚 Reference
- **[Blueprint Schema](reference/blueprint-schema.md)**: Full generic-blueprint YAML schema reference.
- **[Step Schema](reference/step-schema.md)**: Step primitive properties, modes, and loop blocks.
- **[Inputs Schema](reference/inputs-schema.md)**: Parameter input types, aliases, and expression syntax.
- **[Assertions Schema](reference/assertions-schema.md)**: Quality assertions and verification operators.
- **[Project Config Schema](reference/config-schema.md)**: Full `.worktree/config.json` specification.

### 🍳 Recipes & Examples
- **[TDD Loop](recipes/tdd-loop.md)**: A blueprint loop that records agent-step placeholder results and runs tests.
- **[AI Code Patcher](recipes/ai-code-patcher.md)**: A blueprint that records agent-step placeholders alongside quality gates.
- **[CI/CD Automation](recipes/ci-cd-automation.md)**: Running headless Worktree blueprints in GitHub Actions.

### 💻 CLI Reference
- **[Workspace Init (`wt init`)](cli/init.md)**: Provision local workspace and configuration defaults.
- **[Status (`wt status`)](cli/status.md)**: Inspect workspace health, catalog, sandboxes, and recorded sessions.
- **[Config (`wt config`)](cli/config.md)**: Display, modify, and validate project configuration.
- **[Run (`wt run`)](cli/run.md)**: Execute a blueprint by name.
- **[Resume (`wt resume`)](cli/resume.md)**: Resume paused blueprint sessions from saved run state.
- **[History (`wt history`)](cli/history.md)**: List and inspect recorded blueprint runs.
- **[Logs (`wt logs`)](cli/logs.md)**: Show a session's run timeline or a step's captured output.
- **[Diff (`wt diff`)](cli/diff.md)**: View the diff captured for an execution session.
- **[Doctor (`wt doctor`)](cli/doctor.md)**: Run registered workspace diagnostics.
- **[Sandbox (`wt sandbox`)](cli/sandbox.md)**: Git worktree isolation.
- **[Blueprint (`wt blueprint`)](cli/blueprint.md)**: Blueprint catalog items across all tiers.
- **[Step (`wt step`)](cli/step.md)**: Step catalog items across all tiers.
