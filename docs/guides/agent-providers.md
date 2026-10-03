# Agent-Step Adapters

An agent step (`type: agent`) sends its interpolated `prompt` to the resolved provider in `direct` mode and requires an active Dovo Git worktree. Under `dovo run --no-worktree`, and for resumed in-place runs, the step fails with `Agent steps require an active git worktree.`

A provider either returns a unified diff or edits the worktree directly; see [`registry.py`](../../src/dovo/core/agents/registry.py) for which identifiers use which kind. A returned diff is validated, checked, and applied to the worktree working tree unstaged. A direct edit that passed the shared patch gate is recorded as-is. Nothing is applied to the source checkout until you apply the worktree.

The step's stdout is one JSON object followed by a newline, shaped by [`AgentStepSummary`](../../src/dovo/engine/executors/models.py) with `status` drawn from [`AgentResponseStatus`](../../src/dovo/core/agents/models.py). A planning or review prompt that changes no files finishes as `no_op` and keeps its findings in `summary`. Any status other than `proposed_patch` or `no_op` fails the step through its `on_failure` policy.

---

## Runtime-Supported Adapters

The runtime-supported adapter identifiers are:

| Identifier | Configuration note |
|---|---|
| `local` | Default adapter identifier. |
| `ollama` | Selectable runtime adapter. |
| `cursor` | Selectable runtime adapter. |
| `gemini` | Selectable runtime adapter. |
| `copilot` | Selectable runtime adapter. |

The configuration schema additionally accepts `openai`, `anthropic`, `azure_openai`, and `custom`. Those values are schema-valid but are not runtime-supported adapter identifiers; `dovo config validate` reports them as `AGENT_PROVIDER_UNSUPPORTED`, and an agent step using one fails adapter selection.

`tools` in an agent step is accepted metadata. It is not enforced.

---

## Configuring an Adapter Identifier

Set an adapter identifier and optional model metadata in `.dovo/config.json`:

```json
{
  "agent": {
    "provider": "local",
    "model": null,
    "endpoint": null,
    "temperature": 0.2,
    "max_tokens": 4096
  }
}
```

You can update these settings with `dovo config set`:

```bash
dovo config set agent.provider ollama
dovo config set agent.model llama3.1
dovo config set agent.temperature 0.1
```

`dovo run <blueprint> --agent <identifier>` overrides the adapter identifier for that run.

---

## Credentials and Diagnostics

Some adapter identifiers have associated environment-variable checks in `dovo doctor`, for example `GEMINI_API_KEY`, `CURSOR_API_KEY`, and `GITHUB_TOKEN`/`GH_TOKEN`. Configure credentials according to the external tool you use, and note that a successful diagnostic does not prove the provider will accept a request.

---

## Next Steps

- Explore the [Blueprint Schema Reference](../reference/blueprint-schema.md).
- Review [Working with Steps](working-with-steps.md) for the exact agent-step and `tools` behavior.
