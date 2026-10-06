# Troubleshooting: Agent Provider Setup

Diagnostic reference for agent adapter setup and runtime failure modes. All provider adapters return classified `AgentResponse` objects and do not raise on expected failure conditions.

**Relevant sources:** `src/dovo/core/agents/`

---

## 1. Copilot (`copilot`)

**Relevant sources:** [`src/dovo/core/agents/copilot.py`](../../src/dovo/core/agents/copilot.py)
Direct-mutation adapter shelling out to GitHub CLI `gh copilot`.

| Symptom | Cause | Resolution |
|:---|:---|:---|
| `status=provider_error`, `"missing GH_TOKEN or GITHUB_TOKEN"` | Neither token set | Export `GH_TOKEN=...` or `GITHUB_TOKEN=...` |
| `status=provider_error`, `"gh is not installed or not on PATH"` | GitHub CLI missing | Install GitHub CLI (`gh`) |
| `status=timeout` | CLI process timed out | Raise `timeout_seconds` on the agent step |
| `status=provider_error`, `"invalid JSONL from Copilot CLI"` | Streamed JSON parse error | Check `gh` and Copilot extension versions |

---

## Cross-Provider Rules

- All setup and preflight failures are non-raising and populate `AgentResponse.errors`.
- Unregistered provider tokens (for example a `dovo run --agent` override) fail an agent step cleanly at adapter resolution (`get_agent_adapter`) with `AGENT_PROVIDER_UNSUPPORTED`; a configured `agent.provider` outside the schema enum is rejected by `dovo config validate`.
- Direct-mutation adapters share timeout and diff-validation behavior through `CliDirectMutationAdapter`.
