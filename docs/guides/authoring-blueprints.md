# Authoring Blueprints

A blueprint is a YAML file in `.dovo/catalog/blueprints/` that lists the commands, scripts or agent prompts to run.

---

## Blueprint Anatomy

A complete blueprint:

```yaml
name: build-and-test
description: Build package artifacts and run the full test suite
summary: Full build & verification pipeline
version: 1
use_worktree: true
timeout_seconds: 300

env:
  NODE_ENV: test
  CI: "true"

inputs:
  target:
    type: string
    description: Target test directory
    default: tests/
    aliases: ["-t", "--target"]

defaults:
  on_failure:
    action: retry
    max_retries: 2
    backoff_ms: 500
    on_max_retries: abort

steps:
  - id: setup-env
    name: Install dependencies
    run: uv sync --all-extras

  - id: run-tests
    name: Execute tests
    run: pytest ${{ inputs.target }}
```

---

## Top-Level Blueprint Fields

### 1. Identity & Metadata
* `name` *(string, optional)*: Unique display name; when omitted, it defaults to the catalog key.
* `description` *(string, optional)*: Longer description of the blueprint's purpose.
* `summary` *(string, optional)*: One-line summary shown in `dovo blueprint list`.
* `version` *(integer | string, default `1`)*: Schema version.

### 2. Execution Controls
* `use_worktree` *(boolean, default `true`)*: Whether to create an isolated Git worktree for execution.
* `timeout_seconds` *(integer, optional)*: Currently has no effect.
* `env` *(map[string, string], optional)*: Currently has no effect.

### 3. Parameter Inputs (`inputs:`)
Declare typed parameters that are set at run time with CLI flags or `-i/--input`:

```yaml
inputs:
  branch:
    type: string
    description: Target branch name
    required: true
    aliases: ["-b", "--branch"]
  retries:
    type: integer
    default: 3
```

See [Parameter Inputs & Expressions](passing-inputs.md).

### 4. Blueprint Defaults (`defaults:`)
Set defaults for every step that does not define its own:

```yaml
defaults:
  on_failure:
    action: retry
    max_retries: 3
    backoff_ms: 1000
    on_max_retries: prompt_user
```

A step's own `on_failure` takes precedence.

---

## Agent Steps

An agent step gets a private scratch directory outside the checkout, so its temporary files stay out of the diff. Other files the agent creates in the checkout do appear in the diff. See [Agent Providers](agent-providers.md#private-scratch).

---

## Creating Blueprints via CLI

Scaffold a blueprint with `dovo blueprint create`:

```bash
dovo blueprint create --name fix-issue
```

This writes a template to `.dovo/catalog/blueprints/<name>.yml`.

---

## Next Steps

- [Working with Steps](working-with-steps.md)
- [Blueprint Schema](../reference/blueprint-schema.md)
