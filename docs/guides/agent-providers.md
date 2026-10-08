# Agent-Step Adapters

An agent step (`type: agent`) sends its interpolated `prompt` to the resolved provider in `direct` mode and requires an active Dovo Git worktree. Under `dovo run --no-worktree`, and for resumed in-place runs, the step fails with `Agent steps require an active git worktree.`

A provider edits the worktree directly, and an edit that passed the shared patch gate is recorded as-is. See [`registry.py`](../../src/dovo/core/agents/registry.py) for the registered providers. Nothing is applied to the source checkout until you apply the worktree.

The step's stdout is one JSON object followed by a newline, shaped by [`AgentStepSummary`](../../src/dovo/engine/executors/models.py) with `status` drawn from [`AgentResponseStatus`](../../src/dovo/core/agents/models.py). A planning or review prompt that changes no files finishes as `no_op` and keeps its findings in `summary`. Any status other than `proposed_patch` or `no_op` fails the step through its `on_failure` policy.

---

## Runtime-Supported Adapters

The runtime-supported adapter identifiers are:

| Identifier | Configuration note |
|---|---|
| `copilot` | Default adapter identifier. Requires `gh` on `PATH` and `GH_TOKEN` or `GITHUB_TOKEN`. |

Choose an installed, registered provider. Other providers are delayed beyond v1; a config that selects one fails `dovo config validate` with a schema error on `agent.provider`.

`tools` in an agent step is accepted metadata. It is not enforced.

---

## Configuring an Adapter Identifier

Set an adapter identifier and optional model metadata in `.dovo/config.json`:

```json
{
  "agent": {
    "provider": "copilot",
    "model": null,
    "endpoint": null,
    "temperature": 0.2,
    "max_tokens": 4096
  }
}
```

You can update these settings with `dovo config set`:

```bash
dovo config set agent.provider copilot
dovo config set agent.model gpt-4.1
dovo config set agent.temperature 0.1
```

`dovo run <blueprint> --agent <identifier>` overrides the adapter identifier for that run.

---

## Credentials and Diagnostics

Some adapter identifiers have associated environment-variable checks in `dovo doctor`, for example `GH_TOKEN` or `GITHUB_TOKEN` for `copilot`. Configure credentials according to the external tool you use, and note that a successful diagnostic does not prove the provider will accept a request.

---

## Private Scratch

Each agent attempt gets its own private temporary directories under the run's session temp directory, outside the Git checkout:

```text
<session temp dir>/steps/<step-id>/agent/<invocation-id>/
  scratch/
  control/
```

- The invocation id is a fresh 32-character hex id for every attempt, so retries, loop iterations, and resumed runs never reuse a directory.
- Only `scratch/` is named to the agent (as `agent_scratch_path` in the prompt). `control/` is host-owned, is never shown to the agent, and grants it no access.
- This routes the agent's temporary files away from the checkout, so they are not part of the proposed diff. It is not OS containment or a permission grant: an unrestricted agent can still create files in the checkout.
- If the session temp directory is missing, unwritable, or overlaps the checkout, the step fails with exit code `203` before the provider runs:

```text
Cannot prepare private scratch for agent step '<step_id>' (AGENT_SCRATCH_UNAVAILABLE): <detail>
Fix: restore access to the session temp directory or correct its storage path.
```

- Completed runs remove the session temp directory, including every scratch directory; failed, paused, cancelled, and `--keep` runs retain it for inspection.

---

## Next Steps

- Explore the [Blueprint Schema Reference](../reference/blueprint-schema.md).
- Review [Working with Steps](working-with-steps.md) for the exact agent-step and `tools` behavior.
