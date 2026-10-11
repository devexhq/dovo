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

A step's `tools` policy decides what the agent may use; the default grants read and write on the worktree and the attempt's private scratch directory, and nothing else. See [Tool Policy](#tool-policy) and the [Step Schema](../reference/step-schema.md).

---

## Tool Policy

`tools` is a structured policy (`allow`, `deny`, `allow_all`) that Dovo enforces through the provider's own controls. It does not add OS containment. Omit it to inherit `agent.tools` from config, or the default when that is absent: read and write on the worktree and on the invocation's private scratch directory, with no shell, network, or MCP. An explicit object replaces the inherited policy whole, and `tools: {}` grants nothing. Rule grammar: [`tool_policy.py`](../../src/dovo/common/tool_policy.py).

The default policy is restrictive. A step that ran shell commands before the policy was enforced needs an explicit `shell` grant (see the profiles below). A provider that cannot enforce a restrictive policy rejects it before running ([`test_base.py`](../../tests/core/agents/test_base.py)), with `AGENT_TOOL_POLICY_UNSUPPORTED` and step exit `203`; `allow_all: true` with no `deny` rules keeps a provider's unrestricted behavior. A refused tool call at run time ends the step as `blocked` (`204`) with the grant that would allow it.

### Copilot

Rendered by [`copilot.py`](../../src/dovo/core/agents/copilot.py); the flags are pinned by [`test_copilot.py`](../../tests/core/agents/test_copilot.py), and the scratch and control directory checks by [`test_tools.py`](../../tests/core/agents/test_tools.py).

- A restrictive policy runs with `--available-tools`, `--no-ask-user` and `--disallow-temp-dir`, never `--allow-all-tools` or `--allow-all-paths`; an unscoped `network` allow renders `--allow-all-urls`. Only the exact scratch directory is added with `--add-dir`, and Copilot's state lives in an isolated `COPILOT_HOME` under the invocation's control directory, so stored approvals and ambient MCP servers cannot widen the policy.
- `allow_all: true` with no `deny` rules is the fully permissive run. With `deny` rules it adds the matching `--deny-tool` / `--deny-url` flags.
- Host rules render for both `https://` and `http://`. A `*.host` rule also matches the bare `host` (Copilot's wildcard), so a `*.host` allow is slightly wider than Dovo's grammar and a `*.host` deny also blocks `host`.
- Shell allow rules are not exclusive: Copilot runs read-only commands such as `git log` without a grant. Shell `deny` rules are enforced.
- Rejected as `AGENT_TOOL_POLICY_UNSUPPORTED`: a policy with no grants, read or write patterns other than `**`, any read or write `deny`, a scratch grant that is not given to every granted path tool, a scratch-only grant, an `mcp` allow for a server that is neither `github-mcp-server` nor defined in your `mcp-config.json`, and a restrictive policy with no invocation context.
- MCP servers come from your own `mcp-config.json` (`$COPILOT_HOME`, else `~/.copilot`), passed by path with `--additional-mcp-config`; Dovo never copies it. Servers in that file that no `mcp` allow names are switched off with `--disable-mcp-server`, and `--disable-builtin-mcps` applies unless the policy allows `github-mcp-server`, which is known without a config and reaches only its default read-only tools. An `mcp` allow `server/tool` or `server/*` is the exclusive gate: MCP tools it does not name are not exposed to the agent. An `mcp` deny renders `--deny-tool` and needs no config. Flags are pinned by [`test_copilot.py`](../../tests/core/agents/test_copilot.py).
- If the installed `gh copilot` rejects a required flag, the step fails with a message to run `copilot update`; Dovo never retries with a permissive policy.

### Tool policy profiles

Set one of these on a step, or once for every agent step under `agent.tools` in `.dovo/config.json` (the same object):

```json
{ "agent": { "tools": { "allow": [{ "capability": "read" }] } } }
```

**Read-only reviewer** reads the worktree; it cannot edit files or run commands:

```yaml
tools:
  allow:
    - capability: read
```

**Dev loop** reads and writes the worktree, and runs shell commands:

```yaml
tools:
  allow:
    - capability: read
    - capability: write
    - capability: shell
```

**Fully open** keeps the provider's unrestricted behavior; `deny` rules still apply:

```yaml
tools:
  allow_all: true
```

Shell grants are not confinement. Copilot checks the path and URL arguments of simple commands such as `cat`, `touch` and `curl`, but `sh -c` and interpreters such as `python3 -c` can read and write outside the worktree and scratch directory, so a policy that grants `shell` is not path-scoped and amounts to broad filesystem access. Network escapes through interpreters were not tested. The seeded `dovo/ai-planner`, `dovo/ai-reviewer` and `dovo/ai-code-patcher` steps declare the read-only and read/write profiles explicitly; workspaces seeded earlier keep their old copies, which run under the default policy until re-seeded.

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
    "max_tokens": 4096,
    "env_passthrough": [],
    "env_mode": "allowlist"
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

## Subprocess Environment

Dovo supplies the agent provider's subprocess environment explicitly. By default (`agent.env_mode: "allowlist"`) the provider sees only:

- a small base of ambient names (`PATH`, `HOME`, `USER`, `LANG`, `LC_ALL`, `TMPDIR`, `TERM`, plus the Windows system names on Windows) and the network/TLS names (certificate bundles, proxies, `DOCKER_HOST`); the exact lists are `BASE_ENV_NAMES`, `WINDOWS_ENV_NAMES`, and `NETWORK_TLS_ENV_NAMES` in [`environment.py`](../../src/dovo/core/agents/environment.py);
- the generated `DOVO_*` step metadata;
- the active provider's own credential, resolved at call time (for `copilot`, `GH_TOKEN`, else `GITHUB_TOKEN`; `COPILOT_GITHUB_TOKEN`, `COPILOT_ALLOW_ALL`, and `COPILOT_MODEL` are never forwarded from the host);
- names you opt in with `agent.env_passthrough`;
- the agent step's own `env:` entries.

`agent.env_passthrough` is a list of host variable names. An entry is either a literal name or a prefix ending in a single `*` (for example `DOCKER_*`); a bare `*`, a `*` anywhere else, `=`, whitespace, and NUL are rejected by `dovo config validate`. Names absent from the host are skipped, and a prefix never forwards a name Dovo reserves. A repo-tier list replaces a global-tier list; the lists are not merged. Edit `config.json` to change it.

`agent.env_mode: "inherit"` starts from the full host environment instead, then layers the same generated, credential, passthrough, and step `env:` values on top.

An agent step's `env:` entries are forwarded even when their names are outside the allowlist and override base and passthrough values. They cannot override generated `DOVO_*` metadata, the provider's credential names, the adapter's model and permission controls, or, for an authored agent step, `DOVO_AGENT_SCRATCH`, `TMPDIR`, `TMP`, and `TEMP`; such a step fails before the provider runs with `AGENT_ENV_OVERRIDE_INVALID`. In `allowlist` mode the four scratch names point at the attempt's private scratch directory, never its `control/` sibling. Authored `command` and `script` steps are unchanged and keep inheriting the ambient environment.

Secret values among the forwarded variables (step `env:` values with a secret-style name suffix, and the password in a proxy URL) are masked in the provider's response.

### Withheld variables

In `allowlist` mode, when host variables were not forwarded, the agent sees their names (never values) in `DOVO_ENV_WITHHELD` (truncated to 4096 characters with a trailing `,+N more`), a fixed first line in its prompt tells it to ask for `agent.env_passthrough`, the console prints `Agent environment filtered: <N> variables withheld.`, and `session.log` gets one `agent_env_filtered` event with the full list (see [`dovo logs`](../cli/logs.md)). `inherit` mode reports nothing.

### Per-run overrides

`dovo run` and `dovo resume` accept `--env-mode {allowlist,inherit}` and a repeatable `--env-passthrough NAME_OR_PREFIX*` for that invocation only. Flag entries are appended to the configured list, `--env-mode` replaces the configured mode, and neither is written to `config.json` or the run state. An invalid entry fails the command before any step starts.

### What this does not protect

The filter applies only to the subprocess environment Dovo supplies. It does not stop an agent from:

- using provider authentication files reachable through `HOME`;
- reading any file it can open, including `.env` files in the checkout;
- running tools that load their own `.env` files (dotenv-style loaders, `docker compose`).

Tests or tools that rely on variables exported only in your host shell need those names in `agent.env_passthrough`. The enforcing tests live in [`tests/core/agents/test_environment.py`](../../tests/core/agents/test_environment.py).

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
Fix:
- restore access to the session temp directory or correct its storage path.
```

- Completed runs remove the session temp directory, including every scratch directory; failed, paused, cancelled, and `--keep` runs retain it for inspection.

---

## Next Steps

- Explore the [Blueprint Schema Reference](../reference/blueprint-schema.md).
- Review [Working with Steps](working-with-steps.md) for the exact agent-step and `tools` behavior.
