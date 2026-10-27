# Remediations

How a `Remediation` (one recovery step with a `message` and an optional `command`) is written and shown. Errors raise it on a `DomainError` (see [exceptions.md](exceptions.md)).

## Remediations originate in core

- **Decision:** every remediation is built in `core/`, `engine/` or `common/` and carried on the result. No formatter supplies remediation text or a fallback.
- **Why:** terminal and JSON output must show the same steps (R3).
- **Exemplar:** `src/dovo/core/worktree/services/show.py`
- **Enforced by:** review only
- **Since:** #639

## Message wording

- **Decision:** the `message` is one step, starting with a capital letter.
  - Every literal the user would type is single-quoted: commands, flags with their values, file paths, config keys and environment variable names (`Run 'dovo init' to create '.dovo/config.json'`).
  - No backticks and no Rich markup.
  - Alternatives are separate remediations, never joined with `, or` or a leading `Or`.
- **Why:** when `command` is null, the quotes alone show where a literal starts and ends, and the terminal prints only the message.
- **Exemplar:** `src/dovo/core/worktree/services/show.py`
- **Enforced by:** review only
  - `src/dovo/core/worktree/services/lifecycle.py` joins two alternatives with `, or` in one remediation; not yet owned.
- **Since:** #639

## When command is set

- **Decision:** `command` holds the exact command the step describes when running it on its own is the whole step, and is null otherwise.
  - A flag (`'--allow-dirty'`), a path or a config key is never a command.
  - Every non-null `command` also appears, single-quoted, in its `message`.
- **Why:** an agent can run `command` directly, so it must be a complete command or nothing.
- **Exemplar:** `src/dovo/core/worktree/services/show.py`
- **Enforced by:** review only
- **Since:** #639

## Remediation fields are never empty

- **Decision:** `Remediation` rejects an empty `message` or an empty `command`; an absent command is null.
- **Why:** an empty string renders as a blank step and cannot be run.
- **Enforced by:** `tests/common/test_base_result.py`
- **Since:** #639

## Placeholders

- **Decision:** a value the user must supply is written `<kebab-case>` in both `message` and `command`, such as `<worktree-id>` or `<blueprint>`. A value the result already knows is filled in, never left as a placeholder.
- **Why:** a filled-in command can be run as is; a placeholder marks exactly what the user must choose.
- **Enforced by:** review only
- **Since:** #639

## One terminal rendering

- **Decision:** every terminal surface renders remediations as one `Fix:` header followed by one `- <message>` line each. The `command` is published only in JSON.
- **Why:** one rendering keeps every command's output alike, and the message already quotes the command.
- **Exemplar:** `src/dovo/common/formatters.py`
- **Enforced by:** review only
  - `src/dovo/common/lock.py` and `src/dovo/common/filesystem/exceptions.py` hand-write their own fix text into the exception message. #1135 gives both failures structured remediations; removing the hand-written text is not yet owned.
- **Since:** #639

## The Fix block's shape

- **Decision:** the shared rendering prints `Fix:` and one `- <message>` line per remediation, and nothing when there are none.
- **Why:** pinning the shape once lets every surface reuse it unchanged.
- **Exemplar:** `src/dovo/common/formatters.py`
- **Enforced by:** `tests/common/test_formatters.py`
- **Since:** #639
