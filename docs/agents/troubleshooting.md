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
| `status=blocked`, `AGENT_PERMISSION_BLOCKED` | Copilot refused a tool call (a `tool.execution_complete` event with `success: false` and a denied or `Permission denied` error) and the worktree diff is empty | Follow the fix text: add the capability under the step's `tools` (or `agent.tools` in `.dovo/config.json`), or remove or narrow the deny rule it names |
| `status=provider_error`, `"invalid JSONL from Copilot CLI"` | Streamed JSON parse error | Check `gh` and Copilot extension versions |

---

## 2. Claude Code (`claude`)

**Relevant sources:** [`src/dovo/core/agents/claude.py`](../../src/dovo/core/agents/claude.py), [`src/dovo/core/agents/claude_stream.py`](../../src/dovo/core/agents/claude_stream.py)
Direct-mutation adapter registered for Claude Code; the invocation itself has not landed. The stream-json rows below are produced once the invocation slice wires in the classifier.

| Symptom | Cause | Resolution |
|:---|:---|:---|
| `status=provider_error`, `"missing ANTHROPIC_API_KEY"` | Key not set or blank | Export `ANTHROPIC_API_KEY=...` |
| `status=provider_error`, `"Claude Code invocation is not available"` | Registration only; the invocation slice has not landed | None yet; use another provider |
| `status=provider_error`, `"invalid stream-json from Claude Code"` | A stdout line was not a JSON object | Check the installed `claude` version |
| `status=provider_error`, `"Claude Code stream ended without a result"` or `"... (exit <code>)"` | The stream was cut off before a result, with exit 0 or with the shown non-zero exit code | Re-run the step |
| `status=provider_error`, `"Claude Code result error (<subtype>)"` | Claude Code reported an error result, such as a bad model or an API failure | Read the quoted result text after the colon |
| `status=provider_error`, `"unsupported Claude Code result status"` | The result subtype is not one Dovo recognizes | Upgrade Dovo or Claude Code |
| `status=provider_error`, `"Installed Claude Code lacks a required flag"` | Claude Code is too old for a flag Dovo passes | Run `claude update` or reinstall Claude Code |
| `status=provider_error`, `"exit <code>"` | Non-zero exit with no output to quote | Run `claude` directly in the worktree |

---

## Cross-Provider Rules

- All setup and preflight failures are non-raising and populate `AgentResponse.errors`.
- Unregistered provider tokens (for example a `dovo run --agent` override) fail an agent step cleanly at adapter resolution (`get_agent_adapter`) with `AGENT_PROVIDER_UNSUPPORTED`; a configured `agent.provider` outside the schema enum is rejected by `dovo config validate`.
- Direct-mutation adapters share timeout and diff-validation behavior through `CliDirectMutationAdapter`.
