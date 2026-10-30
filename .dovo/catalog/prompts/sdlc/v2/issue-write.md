# Write an issue

Turn a person's description of a change into a GitHub issue in the short format of [templates/issue.md](templates/issue.md). The issue records what the person decided; planning grounds it in the code later, so ground only enough to name dependencies and to catch work that has already shipped.

## 1. Read the request

Read the person's description and anything they point to. Separate what they decided (the problem, the behaviour they want, trade-offs they settled, values they chose) from what they guessed about the code. Only the first goes into the issue.

## 2. Ground lightly

Ground against `origin/main` (`git fetch origin main`), reading only; never edit code or run tooling that mutates or validates.

- **Dependencies:** search open issues for work this one needs first (`gh issue list --state open --search "<terms>"`). Each becomes a **Depends on** item stating what this issue assumes it delivers.
- **Already shipped:** check that no outcome is already delivered: the CLI's help (`uv run dovo <command> --help`), merged pull requests (`gh pr list --state merged --search "<terms>"`) and the code (`rg`). Drop an outcome that has shipped, and tell the person.
- **Owners:** for what the issue leaves out, find the open issue that owns it, so **Out of scope** can name it.

Record none of what the grounding finds in the code: paths, symbols, revision numbers, column lists, test names and counts stay out of the issue.

## 3. Draft

Write the title and body to the template, its sections in order:

- **Why** in the person's terms.
- One **Outcome** per observable behaviour, numbered from `O-1`.
- One **Decision** per trade-off the person settled, numbered from `D-1`, with its cost. Never invent one: when the description leaves a trade-off open, ask the person, and when they leave it open, leave it out for planning to raise as an open question.
- **Binding values** only for literals the person chose.
- **Done when** naming the outcomes by id, plus any check beyond them.

No hard line wraps: each paragraph and list item is one line. The same holds for every comment and pull request text that sdlc v2 posts.

## 4. Check

Run the template's checklist over every sentence and over the whole issue. Then show the person the title and body, and publish only when they ask: `gh issue create --title "<title>" --body-file <file> --label dovo-size-<s|m|l>`, choosing the size by the descriptions of the repository's `dovo-size-*` labels. Add `--blocked-by <n>` for each **Depends on** issue that must merge first.
