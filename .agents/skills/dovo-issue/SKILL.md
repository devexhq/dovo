---
name: dovo-issue
description: >-
  Write or revise a GitHub issue for Dovo CLI in the short or bug format, the
  formats `ship` delivers. Invoked as /dovo-issue. Use when asked to create,
  draft, or revise a GitHub issue.
---

# dovo-issue

An issue records what a person decided, in the short format of [issue.md](../../../.dovo/catalog/templates/issue/issue.md), or the bug format of [bug.md](../../../.dovo/catalog/templates/issue/bug.md) for behaviour that is wrong today. Delivery grounds it in the code later: `dovo run ship -i <n>` (or `/ship <n>`) assesses the issue against `main`, plans it when a design choice needs settling, implements it and opens its one pull request.

## Write or revise

Follow [issue-write.md](../../../.dovo/catalog/templates/issue/issue-write.md): read the request, ground lightly against `origin/main`, draft to the template, run its checklist, and publish only when the person asks. To revise an existing issue, draft its new body the same way and replace it with `gh issue edit <n> --body-file <file>` once the person approves.

When the request comes from a `/dovo-pi` seed (`.agentic/pi/seed-F<n>.md`) or asks for it, also pass `--label found:dogfooding` to `gh issue create` and keep the seed's `<!-- dovo-pi:fp=<hash> -->` marker, verbatim, as the last line of the body.
