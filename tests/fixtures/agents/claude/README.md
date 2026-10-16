# Claude Code stream-json fixtures

Scrubbed logs captured from real `claude` runs, loaded with `load_agent_fixture("claude", "<scenario>")`.

- **CLI version:** 2.1.299 (Claude Code)
- **Captured:** 2026-10-14, model `haiku` (`claude-haiku-5-5`)
- **Command:** `claude -p <prompt> --output-format stream-json --verbose --permission-mode dontAsk --model haiku --allowedTools <tools> --setting-sources "" --no-session-persistence`, run from an empty scratch directory.

## Deviation from the issue

`--bare` was **not** used: it requires `ANTHROPIC_API_KEY`, and these runs used a claude.ai subscription login.
Event shapes (`assistant`, `user`, `result`) are the same, but `system/init` reports `apiKeySource` for that login and lists
no hooks, skills, plugins or MCP servers. Re-capture with `--bare` if the init shape matters.

## Scenarios

| Fixture | Scenario | Terminal `result` |
|---|---|---|
| `planning` | read-only, text answer | `success` |
| `reviewing` | read-only review | `success` |
| `implementing_create` | `Write` creates a file | `success` |
| `implementing_edit` | `Edit` changes an existing file | `success` |
| `permission_denied` | `Bash` not granted; exits 0, listed in `permission_denials` | `success` |
| `permission_denied_by_rule` | `Bash` refused by a deny rule; exits 0, listed in `permission_denials` | `success` |
| `tool_failure` | `Read` of a missing file, `tool_result.is_error` true | `success` |
| `provider_error` | invalid model; CLI exits 1 | `is_error: true`, `terminal_reason: api_error` |
| `unmapped_tool` | unmapped `ToolSearch` and `WebFetch` calls (both `WebFetch` calls failed with `getaddrinfo ENOTFOUND`); captured with CLI 2.1.300 | `success` |
| `truncated_no_result` | copy of `planning` with the final `result` event removed | none |

`provider_error` stderr was `[claude-code:unrecognized_model] {"model":"not-a-real-model","query_source":"sdk"}`; the
other runs wrote only a stdin-wait warning to stderr.

## Scrubbing

Paths are rewritten to `/work`, session/message/tool/request ids and uuids to numbered placeholders (`session-1`,
`toolu-2`, ...), the username to `user`, thinking payloads and signatures are blanked, and `system/init` is reduced to
the built-in tools with empty skill, plugin, agent and MCP lists. `tests/harness/test_agent_fixtures.py` scans for leaks.
