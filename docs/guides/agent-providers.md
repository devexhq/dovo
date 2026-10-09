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
