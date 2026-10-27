# Remediations

How a `Remediation` (one recovery step with a `message` and an optional `command`) is written and shown. Errors raise it on a `DomainError` (see [exceptions.md](exceptions.md)).

## Remediations originate in core

- **Decision:** every remediation is built in `core/`, `engine/` or `common/` and carried on the result.
  - No formatter supplies remediation text or a fallback.
  - No exception message carries hand-written fix text; the steps travel as `Remediation` values.
- **Why:** terminal and JSON output must show the same steps (R3).
- **Since:** #639

## Message wording

- **Decision:** the `message` is one step, starting with a capital letter.
  - Every literal the user would type is single-quoted: commands, flags with their values, file paths, config keys and environment variable names (`Run 'dovo init' to create '.dovo/config.json'`).
  - No backticks and no Rich markup.
  - Alternatives are separate remediations, never joined with `, or` or a leading `Or`.
- **Why:** when `command` is null, the quotes alone show where a literal starts and ends, and the terminal prints only the message.
- **Since:** #639

## When command is set

- **Decision:** `command` holds the exact command the step describes when running it on its own is the whole step, and is null otherwise.
  - A flag (`'--allow-dirty'`), a path or a config key is never a command.
  - Every non-null `command` also appears, single-quoted, in its `message`.
  - Neither field is ever an empty string; an absent command is null.
- **Why:** an agent can run `command` directly, so it must be a complete command or nothing.
- **Since:** #639

## Placeholders

- **Decision:** a value the user must supply is written `<kebab-case>` in both `message` and `command`, such as `<worktree-id>` or `<blueprint>`.
  - A value the result already knows is filled in, never left as a placeholder.
- **Why:** a filled-in command can be run as is; a placeholder marks exactly what the user must choose.
- **Since:** #639

## One terminal rendering

- **Decision:** every terminal surface renders remediations with the shared `format_remediations` block.
  - It prints `Fix:` and one `- <message>` line per remediation, and nothing when there are none.
  - The `command` is published only in JSON.
- **Why:** one rendering keeps every command's output alike, and the message already quotes the command.
- **Since:** #639
