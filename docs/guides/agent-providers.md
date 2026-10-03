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

## Next Steps

- Explore the [Blueprint Schema Reference](../reference/blueprint-schema.md).
- Review [Working with Steps](working-with-steps.md) for the exact agent-step and `tools` behavior.
