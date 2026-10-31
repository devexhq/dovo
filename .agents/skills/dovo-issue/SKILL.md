---
name: dovo-issue
description: >-
  Write or revise a GitHub issue for Dovo CLI in the short or bug format, the
  only formats the slice flow plans, and check whether an existing issue is
  ready to plan. Invoked as /dovo-issue; /dovo-issue ready <n> checks issue <n>
  against main and reports its verdict. Use when asked to create, draft,
  revise, or ready a GitHub issue.
---

# dovo-issue

An issue records what a person decided, in the short format of [templates/issue.md](../../../.dovo/catalog/prompts/sdlc/v2/templates/issue.md), or the bug format of [templates/bug.md](../../../.dovo/catalog/prompts/sdlc/v2/templates/bug.md) for behaviour that is wrong today. Planning grounds it in the code later: `dovo run issue-plan-v2 -i <n>` readies and slices it, and `dovo run issue-code-v2 -i <n>` builds it slice by slice.

## Write or revise

Follow [issue-write.md](../../../.dovo/catalog/prompts/sdlc/v2/issue-write.md): read the request, ground lightly against `origin/main`, draft to the template, run its checklist, and publish only when the person asks. To revise an existing issue, draft its new body the same way and replace it with `gh issue edit <n> --body-file <file>` once the person approves.

When the request comes from a `/dovo-pi` seed (`.agentic/pi/seed-F<n>.md`) or asks for it, also pass `--label found:dogfooding` to `gh issue create` and keep the seed's `<!-- dovo-pi:fp=<hash> -->` marker, verbatim, as the last line of the body.

## Ready mode

`/dovo-issue ready <n>` follows [issue-ready.md](../../../.dovo/catalog/prompts/sdlc/v2/issue-ready.md) for issue `<n>`: it writes `.agentic/issue-ready.md` and `.agentic/issue-ready.json` and never edits the issue, its labels or its comments. Report the verdict and its findings. `issue-plan-v2` runs the same check and labels the issue `dovo:ready` once a person approves; run by hand, ask before applying anything.
