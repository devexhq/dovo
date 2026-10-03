# Troubleshooting: Agent Provider Setup

Diagnostic reference for agent adapter setup and runtime failure modes. All provider adapters return classified `AgentResponse` objects and do not raise on expected failure conditions.

**Relevant sources:** `src/dovo/core/agents/`

---

## 1. Local (`local`)

**Relevant sources:** [`src/dovo/core/agents/local.py`](../../src/dovo/core/agents/local.py)
Subprocess agent communicating over JSON stdin/stdout (`DOVO_LOCAL_AGENT_CMD`).

| Symptom | Cause | Resolution |
|:---|:---|:---|
| `status=provider_error`, `"failed to start ..."` | Command binary not on `PATH` | Install local agent binary or set `DOVO_LOCAL_AGENT_CMD` |
| `status=timeout` | Process exceeded `timeout_seconds` | Increase `agent.timeout_seconds` or optimize agent execution |
| `status=provider_error`, `"invalid JSON on stdout"` | Binary did not emit JSON | Ensure local agent prints valid `LocalAgentStdout` JSON |
| `status=provider_error`, `"stdout JSON failed schema validation"` | Missing/extra keys in JSON | Match exact `LocalAgentStdout` schema |

---

## 2. Ollama (`ollama`)

**Relevant sources:** [`src/dovo/core/agents/ollama.py`](../../src/dovo/core/agents/ollama.py)
Direct HTTP client to Ollama API (`<endpoint>/api/chat`).

| Symptom | Cause | Resolution |
|:---|:---|:---|
| `status=provider_error`, `"ollama requires a non-empty model"` | `agent.model` not configured | Configure model: `dovo config set agent.model <name>` |
| `status=provider_error`, `"invalid Ollama endpoint"` | Endpoint is not absolute URL | Set valid endpoint in config or `OLLAMA_HOST` env var |
| `status=provider_error`, `"failed to reach Ollama at ..."` | Connection refused / server down | Start Ollama server (default `http://127.0.0.1:11434`) |
| `status=timeout` | HTTP request timed out | Increase `agent.timeout_seconds` or use a smaller model |
| `status=provider_error`, `"Ollama HTTP <status>"` | Missing model or HTTP error | Pull model: `ollama pull <model>` |
| `status=unfixable`, `unfixable_reason=model_output_unparseable` | Model output was not JSON | Prompt/model formatting issue; retry or change model |

---

## 3. Cursor (`cursor`)

**Relevant sources:** [`src/dovo/core/agents/cursor.py`](../../src/dovo/core/agents/cursor.py)
Direct-mutation adapter using `cursor-sdk`.

| Symptom | Cause | Resolution |
|:---|:---|:---|
| `status=provider_error`, `"cursor requires a non-empty model"` | `agent.model` unset | Set model: `dovo config set agent.model <name>` |
| `status=provider_error`, `"missing CURSOR_API_KEY"` | API key env var missing | Export `CURSOR_API_KEY=...` |
| `status=provider_error`, `"cursor-sdk is not installed"` | Missing python dependency | Install optional dependency: `pip install ".[cursor]"` |
| `status=timeout` | SDK call timed out | Increase `agent.timeout_seconds` |

---

## 4. Gemini (`gemini`)

**Relevant sources:** [`src/dovo/core/agents/gemini.py`](../../src/dovo/core/agents/gemini.py)
Direct-mutation adapter shelling out to `gemini` CLI.

| Symptom | Cause | Resolution |
|:---|:---|:---|
| `status=provider_error`, `"missing GEMINI_API_KEY"` | API key env var missing | Export `GEMINI_API_KEY=...` |
| `status=provider_error`, `"gemini is not installed or not on PATH"` | CLI binary not installed | Install Gemini CLI tool |
| `status=timeout` | CLI process timed out | Increase `agent.timeout_seconds` |
| `status=provider_error`, Non-zero exit code details | CLI process failure | Check embedded stderr/stdout for provider diagnostic |

---

## 5. Copilot (`copilot`)

**Relevant sources:** [`src/dovo/core/agents/copilot.py`](../../src/dovo/core/agents/copilot.py)
Direct-mutation adapter shelling out to GitHub CLI `gh copilot`.

| Symptom | Cause | Resolution |
|:---|:---|:---|
| `status=provider_error`, `"missing GH_TOKEN or GITHUB_TOKEN"` | Neither token set | Export `GH_TOKEN=...` or `GITHUB_TOKEN=...` |
| `status=provider_error`, `"gh is not installed or not on PATH"` | GitHub CLI missing | Install GitHub CLI (`gh`) |
| `status=timeout` | CLI process timed out | Increase `agent.timeout_seconds` |
| `status=provider_error`, `"invalid JSONL from Copilot CLI"` | Streamed JSON parse error | Check `gh` and Copilot extension versions |

---

## Cross-Provider Rules

- All setup and preflight failures are non-raising and populate `AgentResponse.errors`.
- Unimplemented provider tokens (`openai`, `anthropic`, `azure_openai`, `custom`) are rejected by `dovo config validate` and fail an agent step cleanly at adapter resolution (`get_agent_adapter`) with `AGENT_PROVIDER_UNSUPPORTED`.
- Direct-mutation adapters share timeout and diff-validation behavior through `CliDirectMutationAdapter`.
