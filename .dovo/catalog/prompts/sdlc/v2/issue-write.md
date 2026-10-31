# Write an issue

Turn a person's description of a change into a GitHub issue in the short format of [templates/issue.md](templates/issue.md). The issue records what the person decided; planning grounds it in the code later, so ground only enough to name dependencies and to catch work that has already shipped.

## 1. Read the request

Read the person's description and anything they point to. Separate what they decided (the problem, the behaviour they want, trade-offs they settled, values they chose) from what they guessed about the code. Only the first goes into the issue.

Then size it against the template's rule that an issue is one independently shippable set of outcomes. When the description covers parts that would each ship on their own, such as the same rule applied to several packages, propose one issue per part to the person before drafting. When the parts share a rule or shared code, the first issue is the foundation: the rule in the architecture docs, the shared code and checks, and one part converted as the pilot. Write the issues in that order, each later one depending on the foundation.

## 2. Ground lightly

Ground against `origin/main` (`git fetch origin main`), reading only; never edit code or run tooling that mutates or validates.

- **Dependencies:** search open issues for work this one needs first (`gh issue list --state open --search "<terms>"`). Each becomes a **Depends on** item stating what this issue assumes it delivers.
- **Already shipped:** check that no outcome is already delivered: the CLI's help (`uv run dovo <command> --help`), merged pull requests (`gh pr list --state merged --search "<terms>"`) and the code (`rg`). Drop an outcome that has shipped, and tell the person.
- **Owners:** for what the issue leaves out, find the open issue that owns it, so **Out of scope** can name it.
- **A bug still happens:** for a bug, follow the steps to reproduce on `main` when they only read state or run in a scratch workspace, and say whether it reproduced. A bug that no longer reproduces is not filed; tell the person which merged pull request fixed it when you can find it.

Record none of what the grounding finds in the code: paths, symbols, revision numbers, column lists, test names and counts stay out of the issue.

## 3. Draft

Write the title and body to the template, its sections in order. A bug, behaviour that is wrong today, uses [templates/bug.md](templates/bug.md): **What happens** and **Steps to reproduce** take the place of **Why**, and **Done when** names the regression test; everything else below holds.

- **Why** in the person's terms.
- One **Outcome** per observable behaviour, numbered from `O-1`, stated as a rule that holds wherever it applies. Where the grounding found many places it applies today, state what they share, with one or two as examples, and leave finding the rest to planning.
- One **Decision** per trade-off the person settled, numbered from `D-1`, with its cost. Never invent one, and leave how the work is done (which field, a check's scope, a doc's wording) to planning. When the description leaves a trade-off open, ask the person, and when they leave it open, leave it out for planning to raise as an open question.
- **Binding values** only for literals the person chose.
- **Done when** naming the outcomes by id, plus any check beyond them.

No hard line wraps: each paragraph and list item is one line, and every issue reference is an explicit `[GH-<n>](https://github.com/devexhq/dovo/issues/<n>)` link. The same holds for every comment and pull request text that sdlc v2 posts, except a `Closes #<n>` line, which GitHub needs as written.

## 4. Check

Run the template's checklist over every sentence and over the whole issue. Then show the person the title and body, and publish only when they ask: `gh issue create --title "<title>" --body-file <file>`. A bug adds `--label type:bug --label priority:<p0-critical|p1-high|p2-medium|p3-low>`, choosing the one priority label by the descriptions of the repository's `priority:*` labels (`gh label list`); any other issue gets no priority label. Add `--blocked-by <n>` for each **Depends on** issue that must merge first.
