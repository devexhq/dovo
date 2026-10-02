# Agent-Step Adapters

An agent step (`type: agent`) currently validates and selects an adapter, then emits a placeholder result. It does not invoke a provider, send a prompt to an LLM, enable tools, inspect files, or edit a sandbox.

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

`tools` in an agent step is accepted metadata. It currently has no execution effect.

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

`wt run <blueprint> --agent <identifier>` overrides the adapter identifier for that run. This still only affects the placeholder agent-step dispatch.

---

## Credentials and Diagnostics

Some adapter identifiers have associated environment-variable checks in `wt doctor`, for example `GEMINI_API_KEY`, `CURSOR_API_KEY`, and `GITHUB_TOKEN`/`GH_TOKEN`. Configure credentials according to the external tool you use, but do not treat configuration or a successful diagnostic as proof that Worktree will invoke that provider: the current agent-step runtime does not make provider calls.

---

## Next Steps

- Explore the [Blueprint Schema Reference](../reference/blueprint-schema.md).
- Review [Working with Steps](working-with-steps.md) for the exact agent-step and `tools` behavior.
