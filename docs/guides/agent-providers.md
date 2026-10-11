# Agent Providers

An agent step (`type: agent`) sends its `prompt` to the configured provider. It requires an active Dovo worktree: under `dovo run --no-worktree`, and for resumed in-place runs, the step fails with `Agent steps require an active git worktree.`

The provider edits the worktree directly. Nothing reaches your source checkout until you apply the worktree.

The step's stdout is one JSON object with a `status` (see [Agent step outcomes](../reference/step-schema.md#agent-step-outcomes)). A planning or review prompt that changes no files finishes as `no_op` and puts its findings in `summary`. Any status other than `proposed_patch` or `no_op` fails the step through its `on_failure` policy.

---

## Supported Providers

| Identifier | Requirements |
|---|---|
| `copilot` | Default. Requires `gh` on `PATH` and `GH_TOKEN` or `GITHUB_TOKEN`. |

Selecting any other provider fails `dovo config validate` with a schema error on `agent.provider`.

---

## Configuration

Set the provider and optional model settings in `.dovo/config.json`:

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

Or with `dovo config set`:

```bash
dovo config set agent.provider copilot
dovo config set agent.model gpt-4.1
dovo config set agent.temperature 0.1
```

`dovo run <blueprint> --agent <identifier>` overrides the provider for one run.

`dovo doctor` checks provider credentials, for example `GH_TOKEN` or `GITHUB_TOKEN` for `copilot`. A passing check does not guarantee the provider will accept a request.

---

## Tool Policy

A step's `tools` field controls what the agent may use. It has three keys: `allow`, `deny` and `allow_all`. Dovo enforces it through the provider's own controls; it is not OS-level containment.

- **Default** (when `tools` and `agent.tools` are both omitted): read and write on the worktree and the attempt's private scratch directory. No shell, network or MCP.
- **Explicit `tools`** replaces any inherited policy entirely. `tools: {}` grants nothing.
- **Config-wide**: set `agent.tools` in `.dovo/config.json` to apply the same object to every agent step.

```json
{ "agent": { "tools": { "allow": [{ "capability": "read" }] } } }
```

A provider that cannot enforce a restrictive policy rejects it before running, with `AGENT_TOOL_POLICY_UNSUPPORTED` and exit `203`. A tool call refused at run time ends the step as `blocked` (`204`) and names the grant that would allow it. Rule syntax is in the [Step Schema](../reference/step-schema.md).

### Profiles

**Read-only reviewer** reads the worktree; it cannot edit files or run commands:

```yaml
tools:
  allow:
    - capability: read
```

**Dev loop** reads and writes the worktree and runs shell commands:

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

!!! warning
    Shell grants are not confinement. `sh -c` and interpreters such as `python3 -c` can read and write outside the worktree and scratch directory, so granting `shell` amounts to broad filesystem access.

The seeded `dovo/ai-planner`, `dovo/ai-reviewer` and `dovo/ai-code-patcher` steps declare the read-only and read/write profiles explicitly. Workspaces seeded earlier keep their old copies, which run under the default policy until re-seeded.

### Copilot behavior

- A restrictive policy never uses `--allow-all-tools` or `--allow-all-paths`. Only the attempt's scratch directory is added beyond the worktree.
- Copilot's state is isolated per attempt, so stored approvals and ambient MCP servers cannot widen the policy.
- `allow_all: true` with no `deny` rules is fully permissive. With `deny` rules, the matching tool and URL denials still apply.
- A `*.host` network rule also matches the bare `host`, so a `*.host` allow is slightly wider and a `*.host` deny also blocks `host`.
- Shell `allow` rules are not exclusive: Copilot runs read-only commands such as `git log` without a grant. Shell `deny` rules are enforced.
- MCP servers come from your own `mcp-config.json` (`$COPILOT_HOME`, else `~/.copilot`); Dovo never copies it. Servers that no `mcp` allow names are switched off. `github-mcp-server` works without a config and exposes only its default read-only tools.
- These policies are rejected with `AGENT_TOOL_POLICY_UNSUPPORTED`: no grants at all, read or write patterns other than `**`, any read or write `deny`, a scratch grant not given to every granted path tool, a scratch-only grant, and an `mcp` allow for a server that is neither `github-mcp-server` nor defined in your `mcp-config.json`.
- If the installed `gh copilot` rejects a required flag, the step fails and tells you to run `copilot update`. Dovo never retries with a more permissive policy.

---

## Subprocess Environment

By default (`agent.env_mode: "allowlist"`) the provider sees only:

- Basic system variables (`PATH`, `HOME`, `USER`, `LANG`, `LC_ALL`, `TMPDIR`, `TERM`, the Windows system variables on Windows) and network/TLS variables (certificate bundles, proxies, `DOCKER_HOST`).
- The generated `DOVO_*` step metadata.
- The provider's own credential (for `copilot`, `GH_TOKEN`, else `GITHUB_TOKEN`). `COPILOT_GITHUB_TOKEN`, `COPILOT_ALLOW_ALL` and `COPILOT_MODEL` are never forwarded.
- Variables you list in `agent.env_passthrough`.
- The step's own `env:` entries.

`agent.env_mode: "inherit"` starts from the full host environment instead and layers the same values on top.

### Passing host variables through

`agent.env_passthrough` is a list of host variable names. Each entry is a literal name or a prefix ending in a single `*` (for example `DOCKER_*`). `dovo config validate` rejects a bare `*`, a `*` anywhere else, `=`, whitespace and NUL. Names missing from the host are skipped, and a prefix never forwards a name Dovo reserves. A repo-level list replaces a global one; they are not merged.

### Step `env:`

A step's `env:` entries are always forwarded, even outside the allowlist, and override base and passthrough values. They cannot override generated `DOVO_*` metadata, the provider's credential names, or the provider's model and permission controls. For agent steps they also cannot override `DOVO_AGENT_SCRATCH`, `TMPDIR`, `TMP` or `TEMP`; such a step fails before the provider runs with `AGENT_ENV_OVERRIDE_INVALID`.

Command and script steps keep inheriting the ambient environment.

Secret values among forwarded variables (step `env:` values with a secret-style name suffix, and the password in a proxy URL) are masked in the provider's response.

### Withheld variables

In `allowlist` mode, when host variables were not forwarded:

- the agent sees their names (never values) in `DOVO_ENV_WITHHELD`, and its prompt starts with a line telling it to ask for `agent.env_passthrough`;
- the console prints `Agent environment filtered: <N> variables withheld.`;
- `session.log` records an `agent_env_filtered` event with the full list (see [`dovo logs`](../cli/logs.md)).

`inherit` mode reports nothing.

### Per-run overrides

`dovo run` and `dovo resume` accept `--env-mode {allowlist,inherit}` and a repeatable `--env-passthrough NAME_OR_PREFIX*`. Both apply to that invocation only. Passthrough entries are appended to the configured list, `--env-mode` replaces the configured mode, and nothing is written to `config.json`. An invalid entry fails the command before any step starts.

### Limits

The filter only controls the environment Dovo supplies. It does not stop an agent from:

- using provider authentication files reachable through `HOME`;
- reading any file it can open, including `.env` files in the checkout;
- running tools that load their own `.env` files (dotenv-style loaders, `docker compose`).

If your tests or tools rely on variables exported only in your host shell, add those names to `agent.env_passthrough`.

---

## Private Scratch

Each agent attempt gets its own scratch directory under the run's session temp directory, outside the Git checkout:

```text
<session temp dir>/steps/<step-id>/agent/<invocation-id>/
  scratch/
  control/
```

- Every attempt gets a fresh directory, so retries, loop iterations and resumed runs never reuse one.
- Only `scratch/` is shown to the agent (as `agent_scratch_path` in the prompt). `control/` is host-owned and never exposed.
- Scratch keeps the agent's temporary files out of the proposed diff. It is not a permission boundary: an unrestricted agent can still create files in the checkout.
- If the session temp directory is missing, unwritable or overlaps the checkout, the step fails with exit code `203` before the provider runs:

```text
Cannot prepare private scratch for agent step '<step_id>' (AGENT_SCRATCH_UNAVAILABLE): <detail>
Fix:
- restore access to the session temp directory or correct its storage path.
```

- Completed runs remove the session temp directory. Failed, paused, cancelled and `--keep` runs retain it for inspection.

---

## Next Steps

- [Blueprint Schema](../reference/blueprint-schema.md)
- [Working with Steps](working-with-steps.md)
