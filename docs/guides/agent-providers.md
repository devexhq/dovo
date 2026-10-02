# Agent-Step Adapters

An agent step (`type: agent`) sends its interpolated `prompt` to the resolved provider in `direct` mode and requires an active Worktree Git sandbox. Under `wt run --no-sandbox`, and for resumed in-place runs, the step fails with `Agent steps require an active Worktree Git sandbox.`

A provider either returns a unified diff or edits the sandbox directly; see [`registry.py`](../../src/worktree/core/agents/registry.py) for which identifiers use which kind. A returned diff is validated, checked, and applied to the sandbox working tree unstaged. A direct edit that passed the shared patch gate is recorded as-is. Nothing is applied to the source checkout until you apply the sandbox.

The step's stdout is one JSON object followed by a newline, shaped by [`AgentStepSummary`](../../src/worktree/core/step/models.py) with `status` drawn from [`AgentResponseStatus`](../../src/worktree/core/agents/models.py). A planning or review prompt that changes no files finishes as `no_op` and keeps its findings in `summary`. Any status other than `proposed_patch` or `no_op` fails the step through its `on_failure` policy.

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

The configuration schema additionally accepts `openai`, `anthropic`, `azure_openai`, and `custom`. Those values are schema-valid but are not runtime-supported adapter identifiers; `wt config validate` reports them as `AGENT_PROVIDER_UNSUPPORTED`, and an agent step using one fails adapter selection.

`tools` in an agent step is accepted metadata. It is not enforced.

---

## Configuring an Adapter Identifier

Set an adapter identifier and optional model metadata in `.worktree/config.json`:

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

You can update these settings with `wt config set`:

```bash
wt config set agent.provider ollama
wt config set agent.model llama3.1
wt config set agent.temperature 0.1
```

`wt run <blueprint> --agent <identifier>` overrides the adapter identifier for that run.

---

## Credentials and Diagnostics

Some adapter identifiers have associated environment-variable checks in `wt doctor`, for example `GEMINI_API_KEY`, `CURSOR_API_KEY`, and `GITHUB_TOKEN`/`GH_TOKEN`. Configure credentials according to the external tool you use, and note that a successful diagnostic does not prove the provider will accept a request.

---

## Next Steps

- Explore the [Blueprint Schema Reference](../reference/blueprint-schema.md).
- Review [Working with Steps](working-with-steps.md) for the exact agent-step and `tools` behavior.
