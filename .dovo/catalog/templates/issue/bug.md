# Bug template

A bug is an issue about behaviour that is wrong today. It follows [issue.md](issue.md) in everything but its opening: in place of **Why**, it says what happens and how to make it happen, so planning can reproduce the bug before fixing it and prove the fix with a test. Every rule of issue.md holds, including its checklist: name only public surface, state outcomes as rules, record only trade-offs as decisions, and link issues as `[GH-<n>](https://github.com/devexhq/dovo/issues/<n>)`.

Use the sections below in this order. What happens, Steps to reproduce, Outcomes and Done when are required; write every other section only when it has something to say. Add no other section. No hard line wraps.

```markdown
## What happens
<What the person ran and what went wrong: the output, error or state they saw, quoted exactly when short, and what they expected instead. Name the version or commit it was seen on.>

## Steps to reproduce
1. <A step a user can take: a command, a config value, a file in the workspace.>

## Depends on
- [GH-<n>](https://github.com/devexhq/dovo/issues/<n>): <what this issue assumes it delivers, which planning checks; and what to do if it does not hold>

## Outcomes
- **O-1** <The correct behaviour, as a rule that holds wherever the bug could occur, not only for the steps above.>

## Decisions
- **D-1** <A trade-off a person settled about the fix, and what it costs.>

## Binding values
- <A literal a person chose, such as a new message>: `<the exact value>`

## Out of scope
- <What a reader might expect the fix to cover and it does not; name the issue that does it, when one does.>

## Done when
<The outcomes that must hold, and a regression test that follows the steps to reproduce and fails before the fix.>
```

## What each section holds

- **What happens:** observed behaviour against expected, in a user's terms. The version or commit dates the report; the steps, not the version, are what planning checks.
- **Steps to reproduce:** the shortest sequence a user can follow from a fresh workspace, using only commands, config and files a user touches. Never a source path or an internal call: planning finds the cause.
- **Outcomes:** the fixed behaviour as rules, numbered `O-1` and on. A bug seen in one command often lives in a shared path; state the rule for every place a user could hit it, with the reproduced case as an example.
- **Done when:** the outcomes by id and the regression test; a test that never failed before the fix proves nothing.

## Checklist

Run [issue.md](issue.md)'s checklist, then:

- The steps start from something a user can set up, and following them on `main` shows what **What happens** describes.
- No sentence guesses the cause. A suspected cause a person checked can go in **What happens** as one line marked as a suspicion; planning confirms or drops it.
