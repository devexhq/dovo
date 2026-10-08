# Authoring Blueprints

Blueprints are declarative YAML files stored in your project's catalog (`.dovo/catalog/`) that specify what commands, scripts, or AI agent prompts to execute.

---

## Blueprint Anatomy

Blueprints are stored under `.dovo/catalog/blueprints/*.yml` and use one generic top-level document structure:

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

## Generic Blueprints

A blueprint may contain sequential steps, assertions, failure policies, and loop blocks. Catalog item type does not restrict whether a blueprint can contain loops.

---

## Top-Level Blueprint Fields

### 1. Identity & Metadata
* `name` *(string, optional)*: Unique display name; when omitted, it defaults to the catalog key.
* `description` *(string, optional)*: In-depth explanation of the blueprint's purpose.
* `summary` *(string, optional)*: Short single-sentence summary shown in `dovo blueprint list`.
* `version` *(integer | string, default `1`)*: Format schema version.

### 2. Execution Controls
* `use_worktree` *(boolean, default `true`)*: Whether to create an isolated Git worktree for execution.
* `timeout_seconds` *(integer, optional)*: Accepted blueprint metadata; the current runtime does not apply it as an overall timeout.
* `env` *(map[string, string], optional)*: Accepted blueprint metadata; the current runtime does not inject it into child steps.

### 3. Parameter Inputs (`inputs:`)
Declare typed parameters that can be customized at runtime via CLI flags or `-i/--input`:

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

For full details, see the [Parameter Inputs Guide](passing-inputs.md).

### 4. Blueprint Defaults (`defaults:`)
You can define blueprint-wide defaults inherited by all child steps that do not specify their own:

```yaml
defaults:
  on_failure:
    action: retry
    max_retries: 3
    backoff_ms: 1000
    on_max_retries: prompt_user
```

If a step defines its own `on_failure`, the step-specific configuration takes precedence.

---

## Agent Steps

An agent step receives a private scratch directory outside the checkout for its temporary files, so those files are not part of the proposed diff. The agent can still create other files inside the checkout, and those do appear in the diff. See [Agent-Step Adapters](agent-providers.md#private-scratch).

---

## Creating Blueprints via CLI

You can generate blueprint template scaffolds using `dovo blueprint create`:

```bash
# Create a new blueprint
dovo blueprint create --name fix-issue

dovo blueprint create --name audit-deps
```

This generates a pre-populated template in `.dovo/catalog/blueprints/<name>.yml`.

---

## Next Steps

- Explore [Working with Steps](working-with-steps.md) to configure commands, agent prompts, and reusable catalog steps.
- Read the [Blueprint Schema Reference](../reference/blueprint-schema.md) for the complete attribute specification.
