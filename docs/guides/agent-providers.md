# Agent Providers

An agent step (`type: agent`) sends its `prompt` to the configured provider. It requires an active Dovo worktree: under `dovo run --no-worktree`, and for resumed in-place runs, the step fails with `Agent steps require an active git worktree.`

The provider edits the worktree directly. Nothing reaches your source checkout until you apply the worktree.

The step's stdout is one JSON object with a `status` (see [Agent step outcomes](../reference/step-schema.md#agent-step-outcomes)). A planning or review prompt that changes no files finishes as `no_op` and puts its findings in `summary`. Any status other than `proposed_patch` or `no_op` fails the step through its `on_failure` policy. An agent that cannot do the task reports it by writing `unfixable.txt` to its [private scratch](#private-scratch), so a step that did nothing because it could not is not mistaken for `no_op`.

---

## Supported Providers

| Identifier | Requirements |
|---|---|
| `copilot` | Default. Requires `gh` on `PATH` and `GH_TOKEN` or `GITHUB_TOKEN`. |
| `claude` | Requires `claude` on `PATH` and one of a `CLAUDE_CODE_OAUTH_TOKEN`, a `claude auth login` session or `ANTHROPIC_API_KEY`, used in that order. |

Naming any provider not listed in `default_agent` or as an `agents` key fails `dovo config validate` with a schema error.

---

## Configuration

Select the default provider with `default_agent` and give each provider its own settings block under `agents` in `.dovo/config.json`:

```json
{
  "default_agent": "copilot",
  "agents": {
    "copilot": {
      "model": "gpt-4.1",
      "endpoint": null,
      "temperature": 0.2,
      "max_tokens": 4096,
      "env_passthrough": [],
      "env_mode": "allowlist"
    },
    "claude": {
      "model": "claude-sonnet-5.5",
      "temperature": 0.1
    }
  }
}
```

Or with `dovo config set`:

```bash
dovo config set default_agent copilot
dovo config set agents.copilot.model gpt-4.1
dovo config set agents.claude.temperature 0.1
```

`dovo run <blueprint> --agent <identifier>` overrides `default_agent` for one run. Every agent setting of that run, including `model`, `tools` and the environment settings, comes from `agents.<identifier>`; the other providers' blocks are not read. A provider with no block runs with the field defaults (no model). The legacy top-level `agent` key is rejected by validation and is not migrated; move its settings under `agents.<provider>`.

`dovo doctor` checks provider credentials, for example `GH_TOKEN` or `GITHUB_TOKEN` for `copilot` and `ANTHROPIC_API_KEY` for `claude`. A passing check does not guarantee the provider will accept a request.

---

## Tool Policy

A step's `tools` field controls what the agent may use. It has three keys: `allow`, `deny` and `allow_all`. Dovo enforces it through the provider's own controls; it is not OS-level containment.

- **Default** (when `tools` and `agents.<provider>.tools` are both omitted): read and write on the worktree and the attempt's private scratch directory. No shell, network or MCP.
- **Explicit `tools`** replaces any inherited policy entirely. `tools: {}` grants nothing.
- **Config-wide**: set `agents.<provider>.tools` in `.dovo/config.json` to apply the same object to every agent step run with that provider.

```json
{ "agents": { "copilot": { "tools": { "allow": [{ "capability": "read" }] } } } }
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

### Claude Code behavior

- **Setup:** install Claude Code so `claude` is on `PATH`, then authenticate it one of the three ways below. Each step runs `claude -p --output-format stream-json --verbose --no-session-persistence`, with `--bare` added in `key` mode, `--model <model>` when the agent settings name one and the rendered tool policy flags after it. The prompt is sent on stdin, never on the command line.
- **Authentication:** Dovo picks the first mode that applies at call time.
    1. `oauth`: a non-blank `CLAUDE_CODE_OAUTH_TOKEN` (create one with `claude setup-token`). Only the token is forwarded, and no login check runs.
    2. `cli`: `claude auth status --json` reports `loggedIn: true` (run `claude auth login`). Neither credential variable is forwarded, so the login session is the credential.
    3. `key`: a non-blank `ANTHROPIC_API_KEY`. Only the key is forwarded, and `--bare` is added.
- **Host configuration:** `cli` and `oauth` modes load your host Claude Code configuration (hooks, plugins, skills, `CLAUDE.md`, MCP servers) beyond the tool policy Dovo renders, which `key` mode skips through `--bare`.
- **Missing authentication:** with none of the three, the step fails with `Claude Code is not authenticated` before `claude` starts, naming `claude auth login`, `CLAUDE_CODE_OAUTH_TOKEN` and `ANTHROPIC_API_KEY`. `dovo doctor` reports the mode in use on its `agent.setup` row.
- **Subprocess environment:** only the credential of the mode in use is forwarded. Inherited `CLAUDE_*` variables are removed and reported as withheld, while `CLAUDE_*` names you list in `agents.claude.env_passthrough`, `--env-passthrough` or a step `env` are kept, except the reserved control names, which are rejected. A credential name (`CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`) listed in `agents.claude.env_passthrough` or `--env-passthrough` is ignored, and one set in a step `env` is rejected; the mode in use decides which credential is forwarded.
- **Missing flag:** if the installed Claude Code does not know a flag Dovo passes, the step fails with `Installed Claude Code lacks a required flag` and tells you to run `claude update`.
- These policies are rejected with `AGENT_TOOL_POLICY_UNSUPPORTED`: an `mcp` allow or deny rule without a pattern, an `mcp` pattern containing a comma, an `mcp` allow for a server defined in neither `agents.claude.mcp_servers` nor your Claude config, an `mcp` allow without an invocation, a `*.host` network pattern in an `allow` or `deny`, a read or write pattern that escapes its root, a read or write path containing a comma, and a scratch rule without an invocation.
- `read` and `write` rules accept the `worktree` and `scratch` roots, glob patterns, and both `allow` and `deny`. They are rendered as `Read(//<root>/<pattern>)` and `Edit(//<root>/<pattern>)`, anchored to the root's absolute path; an absent pattern is `**`. A `scratch` allow adds the attempt's scratch directory. `shell` and `network` rules are accepted, including the default policy. Under `allow_all: true`, `allow` rules other than `mcp` are not checked; `deny` rules are. With `allow_all: true` and `deny` rules, the attempt's scratch directory is not added.
- MCP servers are loaded only when an `mcp` allow rule names them, including under `allow_all`. Each rule `server/tool` or `server/*` resolves `server` from `agents.claude.mcp_servers`, else from the top-level `mcpServers` of your Claude config (`$CLAUDE_CONFIG_DIR/.claude.json`, else `~/.claude.json`); a name in both takes the `agents.claude.mcp_servers` entry. Entries from your Claude config are written as-is and are not checked for literal secrets. Only the named servers are written to a config file in the attempt's private control directory, and the grant is `mcp__<server>__<tool>` or `mcp__<server>`. Project `.mcp.json`, local-scope and plugin servers are never loaded, and Claude Code always runs with `--strict-mcp-config`. With no `mcp` allow rule it runs with no MCP servers. `mcp` `deny` rules `server/tool` and `server/*` are enforced. The config file is written before `claude` starts; a write failure fails the step and starts no process.
- `agents.claude.mcp_servers` maps a server name to a `stdio` (`command`, `args`, `env`) or `http`/`sse` (`url`, `headers`) definition, see [`McpServerDefinition`](https://github.com/devexhq/dovo/blob/main/src/dovo/common/mcp_servers.py). A name must not contain `/`, `*`, `,`, `__` or whitespace. Every `env` and `headers` value must contain a `${NAME}` reference, so the config never stores a secret; Claude Code expands it from the attempt's environment, so list the variable in `agents.claude.env_passthrough`. Copilot does not read `agents.copilot.mcp_servers`.

!!! warning
    A `stdio` MCP server is a process Claude Code starts; it is not bound by the `read` and `write` path policy.

- A `deny` rule always wins over an `allow` rule.
- Shell `allow` rules are not exclusive: Claude Code runs a built-in set of read-only commands such as `ls` and `grep` without a grant. Shell `deny` rules are enforced.

---

## Subprocess Environment

By default (`agents.<provider>.env_mode: "allowlist"`) the provider sees only:

- Basic system variables (`PATH`, `HOME`, `USER`, `LANG`, `LC_ALL`, `TMPDIR`, `TERM`, the Windows system variables on Windows) and network/TLS variables (certificate bundles, proxies, `DOCKER_HOST`).
- The generated `DOVO_*` step metadata.
- The provider's own credential (for `copilot`, `GH_TOKEN`, else `GITHUB_TOKEN`). `COPILOT_GITHUB_TOKEN`, `COPILOT_ALLOW_ALL` and `COPILOT_MODEL` are never forwarded.
- Variables you list in `agents.<provider>.env_passthrough`.
- The step's own `env:` entries.

`agents.<provider>.env_mode: "inherit"` starts from the full host environment instead and layers the same values on top.

### Passing host variables through

`agents.<provider>.env_passthrough` is a list of host variable names. Each entry is a literal name or a prefix ending in a single `*` (for example `DOCKER_*`). `dovo config validate` rejects a bare `*`, a `*` anywhere else, `=`, whitespace and NUL. Names missing from the host are skipped, and a prefix never forwards a name Dovo reserves. A repo-level list replaces a global one; they are not merged.

### Step `env:`

A step's `env:` entries are always forwarded, even outside the allowlist, and override base and passthrough values. They cannot override generated `DOVO_*` metadata, the provider's credential names, or the provider's model and permission controls. For agent steps they also cannot override `DOVO_AGENT_SCRATCH`, `TMPDIR`, `TMP` or `TEMP`; such a step fails before the provider runs with `AGENT_ENV_OVERRIDE_INVALID`.

Command and script steps keep inheriting the ambient environment.

Secret values among forwarded variables (step `env:` values with a secret-style name suffix, and the password in a proxy URL) are masked in the provider's response.

### Withheld variables

In `allowlist` mode, when host variables were not forwarded:

- the agent sees their names (never values) in `DOVO_ENV_WITHHELD`, and its prompt starts with a line telling it to ask for `agents.<provider>.env_passthrough`;
- before the agent starts, the console prints `! Agent environment filtered: <N> variables withheld.`; when the attempt does not succeed it also prints a `! Fix:` line saying how to pass names through (`agents.<provider>.env_passthrough` or `--env-passthrough`);
- `session.log` records an `agent_env_filtered` event with the full list (see [`dovo session logs`](../cli/session.md#dovo-session-logs)).

`inherit` mode reports nothing.

### Per-run overrides

`dovo run` and `dovo resume` accept `--env-mode {allowlist,inherit}` and a repeatable `--env-passthrough NAME_OR_PREFIX*`. Both apply to that invocation only. Passthrough entries are appended to the configured list, `--env-mode` replaces the configured mode, and nothing is written to `config.json`. An invalid entry fails the command before any step starts.

### Limits

The filter only controls the environment Dovo supplies. It does not stop an agent from:

- using provider authentication files reachable through `HOME`;
- reading any file it can open, including `.env` files in the checkout;
- running tools that load their own `.env` files (dotenv-style loaders, `docker compose`).

If your tests or tools rely on variables exported only in your host shell, add those names to `agents.<provider>.env_passthrough`.

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
- The prompt tells the agent to write the reason, in 100 words or less, to `<agent_scratch_path>/unfixable.txt` and stop when it cannot complete the instruction. A non-blank file makes the attempt `unfixable` (exit code `201`) with the stripped text as `unfixable_reason`, and the step fails with `Agent reported the task unfixable: <reason>`. Dovo keeps the whole text, whatever its length. A blank file is ignored; a file that cannot be read, a symlink, or anything other than a regular file (such as a FIFO) gives `provider_error`; a timeout or runner error wins over the file. Writing the file needs write access to the `scratch` root, which the default policy grants; a step whose `tools` policy withholds it cannot write the file and ends `blocked` or `no_op` instead.
- If the session temp directory is missing, unwritable or overlaps the checkout, the step fails with exit code `203` before the provider runs:

```text
Cannot prepare private scratch for agent step '<step_id>' (AGENT_SCRATCH_UNAVAILABLE): <detail>
Fix:
- restore access to the session temp directory or correct its storage path.
```

- Completed runs remove the session temp directory. Failed, paused, cancelled and `--keep` runs retain it for inspection.

---

## Rejected Edits

When an agent edits the worktree and the result trips a patch limit (`max_files`, patch size, a binary change, or an unsafe path), Dovo saves the full change set as a patch before resetting the worktree, at `~/.dovo/storage/projects/<project_id>/sessions/<session_id>/rejected/<step_id>_attempt_<n>.patch` (`<step_id>_iter_<k>_attempt_<n>.patch` inside a loop; the root honors `DOVO_HOME`). The step error ends with `Discarded edit saved to <path>.`, and `dovo session show` lists the same path. Recover the work with `git apply <path>` from a worktree at the same baseline. If the patch cannot be written, the worktree keeps the agent's edits and the step fails with `Could not save the rejected edit (<reason>); the worktree was left unchanged.` Saved patches are kept as long as the session.

Edits from an attempt whose agent reported the task [unfixable](#private-scratch) are saved and discarded the same way. The step error then starts with `Agent reported the task unfixable: <reason>`, followed by `Discarded edit saved to <path>.`

---

## Attempt Logs and Heartbeat

An agent attempt streams the provider's output while it runs. Each stdout and stderr line is masked and appended to the attempt's capture files, so a run in progress can be inspected on disk with `dovo session logs --step <step_id>`. Capture follows `history.save_attempt_logs`. The terminal is not given the provider's raw lines. For a provider that reports progress (today, Copilot and Claude Code) it shows one bullet entry per action the agent takes (`● Read|Ran|Edited|Searched <target>`, with shell commands syntax-highlighted), and each action is also recorded as an `agent_progress` event in `session.log`. Claude Code additionally shows the first line of any text the agent wrote before an action as its own bullet, and the first lines of each tool's output under the action behind a `⎿` connector: a read shows its line count, an edit its first line, and longer output ends with `… +<N> lines`. Text still pending when the attempt ends, such as the closing message, is not shown. When 30 seconds pass without progress it shows `agent running (<elapsed>s)` until the attempt ends. A provider that reports no progress shows only the heartbeat line. With `--format json` each entry is an `AgentProgressEvent` envelope whose payload carries `step_id`, `kind` (`edit`, `read`, `search`, `shell`, `note` or `result`), `target`, `call_kind`, `line_count` and `is_error`. The step's own stdout remains the single JSON summary line; the terminal renders it as a `Result: <status>` block with the summary text and any touched files, while `--format json` carries the raw line.

---

## Next Steps

- [Blueprint Schema](../reference/blueprint-schema.md)
- [Working with Steps](working-with-steps.md)
