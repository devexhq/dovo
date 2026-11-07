# Step Schema Reference

YAML schema for steps, loop blocks and `if` blocks.

---

## `StepDefinition` Fields

Every standard step accepts the following fields:

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `id` | `string` | **Yes** | Auto-generated | Unique step identifier. Auto-derived from `name` (slugified) if omitted in blueprint. |
| `name` | `string` | No | `null` | Display name shown during execution progress. |
| `description` | `string` | No | `null` | Optional description of the step's operation. |
| `run` | `string` | Conditional | `null` | Shorthand shell command. It cannot be combined with `uses` or inline-type-only fields. |
| `uses` | `string` | Conditional | `null` | Reference to a reusable step ID (`dovo/*` or catalog step). Other mode-specific fields are not rejected. |
| `type` | `string` | Conditional | `null` | Primitive type: `command`, `agent`, `script`, or `ask`. |
| `command` | `string` | Conditional | `null` | Shell command string. Required when `type: command`. |
| `prompt` | `string` | Conditional | `null` | Instruction sent to the agent provider after interpolation; it must not be blank. Required when `type: agent`. |
| `script_path` | `string` | Conditional | `null` | Relative path to local script. Required when `type: script`. |
| `tools` | `object` | No | omitted | Tool policy with `allow`, `deny` (lists of `{capability, root, pattern}` rules) and `allow_all`. See [Tool Policy](../guides/agent-providers.md#tool-policy). Omitted means the configured default (read and write on the worktree and scratch only). An explicit object replaces any inherited policy, `tools: {}` included; a plain string list is rejected. Capabilities are `shell` (exact command or `cmd *` prefix), `read`/`write` (root-relative glob, optional `root: worktree\|scratch`), `network` (host or `*.host`) and `mcp` (`server/tool` or `server/*`). |
| `env` | `map[string, string]` | No | `{}` | Step-specific environment variables, forwarded to command, script, and agent steps. Supports `${{ inputs.* }}` interpolation. |
| `timeout_seconds`| `integer` | No | `120` | Maximum execution duration (seconds, greater than 0). |
| `assert` | `StepAssert` | No | `null` | Verification criteria. See [Assertions Schema](assertions-schema.md). |
| `on_failure` | `string \| FailureSpec` | No | `abort` | Failure handling policy or detailed retry object. |
| `artifacts` | `list[ArtifactPublishSpec]` | No | `[]` | Files to publish as artifact revisions when the step succeeds. See [`artifacts` Entries](#artifacts-entries). |
| `max_files` | `integer` | No | `null` | Agent steps only: most files one attempt's patch may touch (at least `1`). Omitted means no limit. See [`max_files` Limit](#max_files-limit). |
| `interaction` | `object` | Conditional | `null` | Prompt shown to a person. Required when `type: ask`, rejected on every other type. See [`interaction` Block](#interaction-block). |
| `when` | `list[string]` | No | `null` | Conditions that must all be true for the step to run. Blueprint steps only. See [`when` Guards](#when-guards). |

### `max_files` Limit

When an attempt's patch touches more distinct files than `max_files`, the patch is rejected. The step's error starts with `Patch touches <count> files; max_files is <max_files>.`

`max_files` is rejected on `run`, `command`, `script`, `internal` and `ask` steps. `uses` steps are not checked at load time.

---

## `artifacts` Entries

Each entry of a step's `artifacts:` list publishes the files matching `path` (a glob relative to the worktree) as the next revision of the artifact `name` once the step's command, assertions and checks have passed.

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `name` | `string` | **Yes** | - | Artifact name, unique per session; each publish adds a new revision, unless its files are identical to the latest revision's, which then stays current. |
| `path` | `string` | **Yes** | - | Glob of files to publish. Not interpolated. |
| `retention_days` | `integer` | No | `null` | Days until the revision expires; `0` or omitted never expires. |
| `required` | `boolean` | No | `true` | When `true`, a glob that matches nothing or any other publish failure fails the step and its `on_failure` policy applies. When `false`, the failure is a warning. |

A failed or `ignored` step publishes nothing. See [`dovo artifacts`](../cli/artifacts.md) for revisions and downloads.

---

## Step Shape Validation Rules

Steps are validated as follows:
1. **Resolution order**: A step needs at least one of `run`, `uses` or `type`, checked in that order.
2. **`run` Exclusions**: When `run` is used, it cannot be combined with `uses`, `command`, `type`, `prompt`, `script_path`, or `tools`.
3. **`uses` Boundary**: When `uses` is present without `run`, other mode-specific fields are not rejected.
4. **Type Field Requirements**:
   - `type: command` → requires `command`
   - `type: agent` → requires `prompt`
   - `type: script` → requires `script_path`
   - `type: ask` → requires `interaction`, and cannot be combined with `run`, `uses`, `command`, `prompt`, `script_path`, `tools`, `artifacts` or `assert`
5. **`interaction` Boundary**: `interaction` is only valid with `type: ask`. Every rule on this page for an ask step is reported as `CATALOG_SCHEMA_INVALID` by `dovo blueprint validate` and `dovo step validate`, with the step id in the message.

---

## `interaction` Block

A `type: ask` step pauses the run for a person's answer. The answer is recorded as the step's outputs; nothing is auto-answered. See [Human Input](../guides/human-input.md) for worked blueprints.

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `mode` | `string` | **Yes** | - | `confirm`, `select`, `multiselect` or `text`. |
| `message` | `string` | **Yes** | - | Question shown to the person. Supports `${{ inputs.* }}`, `{{ steps.* }}` and `{{ artifacts.* }}` placeholders. |
| `artifact` | `string` | No | `null` | Artifact shown above the options; its latest revision and digest are recorded. A single text file is shown, cut at 200 lines; any other artifact lists its files with sizes. |
| `comment` | `object` | No | `{policy: disabled, message: Comment}` | Typed-comment policy (`disabled`, `optional`, `required`) and prompt text. Rejected in `text` mode. |
| `options` | `list` | Conditional | `null` | Static options. |
| `options_from` | `object` | Conditional | `null` | Options read from a JSON array inside an artifact file. |

| Mode | Options | Answer |
|---|---|---|
| `confirm` | omitted, or exactly 2. Omitted means `approve` (Approve) and `deny` (Deny, `fail: true`). | one option |
| `select` | at least 2, from `options` or `options_from` | one option |
| `multiselect` | at least 1, from `options` or `options_from` | zero or more options |
| `text` | none | one non-empty text |

Each option takes `value` (`^[A-Za-z0-9_.-]{1,64}$`, unique within the prompt), `label` (non-empty), `fail` (default `false`; `confirm` and `select` only) and `comment` (`disabled`, `optional` or `required`, overriding the interaction default). Answering a `fail: true` option records the outputs and fails the step with exit code `1` and the error `Prompt '<id>' answered '<value>'.`, so the step's `on_failure` applies.

`options_from` replaces `options` (give one, not both) and takes `artifact`, `file` (a relative path of one file in that artifact), `items_path` (dot path to a JSON array), `value_field` and `label_field`; values are converted with `str()`. The latest revision at ask time is used and the file is validated before anything is shown. A missing artifact, an unreadable or invalid file and duplicate values fail the step; so does a `select` with fewer than 2 items. An empty array in `multiselect` is not an error: no prompt is shown and the step completes with an empty selection.

With several chosen options in a `multiselect`, the strictest comment policy among them applies (`required` over `optional` over `disabled`).

### Ask step outputs

All values are strings.

| Output | Value |
|---|---|
| `selected` | `confirm` / `select`: the option value. `multiselect`: a JSON array of values in option order. `text`: empty. |
| `selected_count` | `1` for `confirm` / `select`, the number chosen for `multiselect`, `0` for `text`. |
| `comment` | The comment, or empty. |
| `text` | The `text` mode answer, else empty. |
| `artifact_revision`, `artifact_digest` | Of `interaction.artifact`, else empty. |

Read them with `{{ steps.<id>.outputs.<key> }}`. In a loop, `steps.<id>.outputs.*` resolves to the most recent finished instance of that step, so iteration 2 sees iteration 1's answer and iteration 1 sees an empty string.

`until` and `when` conditions read the same recorded outputs. `selected_count` is a string, and a string holding an integer or a decimal compares numerically against a number, so `steps.triage.outputs.selected_count == 0` and `steps.triage.outputs.selected_count > 0` work as written.

---

## `on_failure` FailureSpec Object

`on_failure` can also be a mapping:

| Field | Type | Default | Allowed Values / Bounds | Description |
|---|---|---|---|---|
| `action` | `string` | `abort` | `abort`, `continue`, `prompt_user`, `retry` | Initial action when a step fails. |
| `max_retries` | `integer` | `3` | at least 1 | Maximum number of retry attempts (when `action: retry`). |
| `backoff_ms` | `integer` | `0` | at least 0 | Milliseconds to sleep between retry attempts. |
| `on_max_retries` | `string` | `abort` | `abort`, `continue`, `prompt_user` | Terminal policy when all retry attempts are exhausted. |

---

## `LoopStepBlock`

Repeats the steps in `do` until an `until` condition is met:

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `id` | `string` | **Yes** | — | Unique identifier for the loop block. |
| `type` | `string` | **Yes** | `loop` | Must be `loop`. |
| `name` | `string` | No | — | Human-readable label for the loop block. |
| `max_iterations` | `integer` | No | `5` | Maximum number of iterations (at least 1). |
| `until` | `list[string]` | **Yes** | — | Termination condition expressions (e.g. `['steps.test.exit_code == 0']`), checked like [`when`](#when-guards) expressions. |
| `do` | `list[StepDefinition]` | **Yes** | — | List of steps to execute sequentially on each iteration. |
| `on_max_iterations` | `string` | No | `prompt_user` | Terminal policy (`abort`, `continue`, `prompt_user`) if loop reaches `max_iterations` without terminating. |
| `when` | `list[string]` | No | `null` | Conditions that must all be true for the loop to start. See [`when` Guards](#when-guards). |

---

## Expression Grammar

A `when` list, an `until` list, an `if` block's `condition` and a `${{ ... }}` / `{{ ... }}` placeholder body all share one grammar: operands joined by operators, in one of these forms, loosest-binding first:

- `left || right` and `left && right` — logical or/and. `||` and `&&` group to the left (`a || b || c` is `(a || b) || c`), and `&&` binds tighter than `||`, so `a || b && c` is `a || (b && c)`.
- `left <op> right` — one comparison; a comparison does not chain, so `a == b && b == c` is written out, never `a == b == c`.
- `!value` — negation. `!` binds to the next `!` or to one value (a call, a projection, an operand or a parenthesised group), never to a whole comparison: `!a == b` reads as `(!a) == b`, so negating a comparison needs parentheses: `!(a == b)`.
- `(expression)` — grouping; parentheses leave no node of their own in the parsed tree.

`&&`/`||` short-circuit: the side not taken is never evaluated, so a path only on that side is never resolved. A source nests `(` and `!` at most 32 deep and holds at most 64 `&&` and `||` joins in all.

| Operator | Compares |
|---|---|
| `==`, `!=` | Equal or not. Both sides compare as numbers when one side is a number and the other a number or a numeric string (an integer or a decimal); otherwise by value, so two strings compare as text and `"3.0" == "3"` is false. |
| `<`, `<=`, `>`, `>=` | Order. True only when one side is a number and the other a number or a numeric string; otherwise always false, two numeric strings included, so ordering one recorded output against another is always false. |
| `contains` | Infix substring or membership: a string's substring, an item of a list or a key of an object, else a substring check on both sides' text. |
| `in`, `not in` | Right side is a bracketed list, `[a, b, c]`. `in` passes when any item equals the left side under `==`; `not in` when every item does under `!=`. |

A `null` on either side, such as a JSON `null` read through an `outputs` key or an artifact's content, makes every comparison false, `!=` included.

A value is one of a quoted string, a number, `true`/`false` (any case), or an operand: a dotted name such as `steps.check.exit_code`, read in the roots the surrounding clause allows (see [`when` Guards](#when-guards) for conditions). A name segment holding a character other than a letter, a digit, `_` or `-`, such as a step id or output key with a dot in it, is written as a quoted bracket key: `steps['build.linux'].outputs['image.tag']`. A placeholder can also pick a finished step by its position in run order with an integer index, `steps[0]` for the first and `steps[-1]` for the most recent (see [Interpolation Paths](#interpolation-paths)); a condition cannot. A value of more than one unquoted word must be quoted, as `"needs edits"` is in `steps.a.status == "needs edits"`.

### Truthiness and absent values

An operand has no value (is absent) when the step, output or artifact it names has not run, finished, published or been recorded yet. An absent value is always false, and a comparison or `in`/`not in` built on one is unresolved: it is false whatever its operator, and a `when` it decides skips its step as [`when_unresolved`](#when-guards). Negating it gives true, so with step `a` not run, `!(steps.a.outputs.x in ["p"])` is true while `steps.a.outputs.x not in ["p"]` is false.

`false`, `0`, `0.0`, `""`, `null` and an absent value are false. Every other value is true, including the strings `"0"` and `"false"`, and an empty list `[]`.

### Functions and projection

| Function | Arguments | Returns |
|---|---|---|
| `contains(haystack, needle)` | 2 | Whether the string `haystack` holds the string `needle`, or the list `haystack` holds an item equal to `needle`. |
| `fromJSON(text)` | 1 | The value the JSON string `text` holds. |
| `join(list[, separator])` | 1–2 | The list's items, each rendered as a placeholder renders a value (below), joined by `separator` (`,` when omitted). |
| `length(value)` | 1 | The length of a string, list or object. |
| `trim(text)` | 1 | `text` without its leading and trailing whitespace. |

A function given an absent argument, or an argument of a type it does not take, returns an absent value at run time instead of failing the run; `fromJSON` of text that is not JSON is absent too.

`<path>.*.<key>` projects: it reads `<key>` from every object of the array `<path>` holds (a string value is read as JSON first), skipping objects that miss the key, and is absent when `<path>` is not an array. A further `.<key2>` after the first key chains to read a nested key from each object, so `steps.list.outputs.items.*.name.first` reads `name.first` from every item. The path before `.*` is the operand a `when` or `until` condition checks against its readable roots.

### Placeholder `||` defaults and rendering

A placeholder runs from `{{` (or `${{`) to the first `}}` outside a quoted string, so `{{ inputs.mode == 'a}}b' }}` is one placeholder; when a quote in the body is never closed, it ends at the first `}}`.

A placeholder stays as written when its body is a lone path no namespace defines, such as a misspelled input or a single unknown word, or a lone quoted string, number or `true`/`false`. The same unknown path inside a larger expression reads as absent, so `{{ inputs.name || 'anonymous' }}` renders `anonymous` when `inputs.name` is undefined: `||` takes its left side only when it is truthy, and an undefined or absent path is never truthy.

Every value renders the way a recorded step output does: a string as is, an absent or `null` value as the empty string, a boolean as `true` or `false`, a list or object as JSON, and anything else as plain text.

### Load errors

`dovo blueprint validate`, `dovo run` and `dovo resume` reject an expression that cannot be parsed, naming the step, loop or placeholder, with `Invalid expression '<source>': <reason>.`:

| Reason | Example source |
|---|---|
| Empty expression | `""` |
| Unquoted text of more than one word | `steps.a.status == needs edits` |
| Unknown function, or the wrong number of arguments | `unknown(1)`, `length()` |
| Unterminated string, or an unsupported `\` escape | `steps.a.stdout == "abc` |
| Unsupported number: a leading `+`, a date, or scientific notation | `steps.a.exit_code == 1e3` |
| Missing or unexpected `(`, `)`, `[`, `]`, or a value after an operator | `steps.a.exit_code ==` |
| Nesting past 32 `(`/`!` deep, or past 64 joins in all | — |

A `when` or `until` list, and an `if` block's `condition`, reject a syntactically valid expression too when an operand reads a root or step the clause cannot, or no operand of the whole expression reads step, iteration or artifact state: see [`when` Guards](#when-guards).

---

## `when` Guards

A `when` list holds condition expressions written exactly as loop `until` conditions are, such as `steps.check.exit_code == 0`. Any step kind and any loop can carry one, including steps inside a loop's `do`.

A `steps.<id>` operand reads one of the step's fields: `status`, `exit_code`, `stdout`, `error_code` or `outputs.<key>`. `error_code` is the error code the step recorded, such as `AGENT_TIMEOUT`, or `""` when it recorded none, so `steps.fix.error_code == ""` is true for a step that succeeded.

`dovo blueprint validate`, `dovo run` and `dovo resume` reject a blueprint, naming the step or loop, when:

- a `when` list is empty or holds an invalid expression, including an unquoted value of more than one word; see [Load errors](#load-errors) for the full list of reasons;
- an expression references a step that is not an earlier step in the same or an enclosing scope. A top-level step sees the top-level steps before it. A step inside `do` also sees the steps before it in the same `do`. A step after an `if` block also sees the steps of either branch, and a step inside a branch sees the steps before the block and the steps before it in the same branch. A loop's or `if` block's own id cannot be referenced, and the steps inside a loop's `do` are not visible after the loop;
- an expression uses `iteration.index` outside a loop's `do`;
- an operand is a dotted name whose root is not `steps`, `iteration` or `artifacts`, as `inputs.<name>` is;
- an `artifacts.<name>.<field>` operand names no field (`content`, `digest`, `dir` or `revision`), as `artifacts.plan` does;
- an operand reads an artifact that no step of the blueprint publishes through its `artifacts:`. A step anywhere in the blueprint counts, inside a loop's `do` or an `if` branch too, but on a `uses:` step only the `artifacts:` written in the blueprint.

An `artifacts.<name>.<field>` operand reads the latest revision of `<name>` published in the current session, when the expression is evaluated. The field is the first of `content`, `digest`, `dir` or `revision` after the name, so a name may contain dots. `revision` is `"2"`, comparing numerically as [Expression Grammar](#expression-grammar) says a numeric string does, and a path after `content` reads the content as JSON, as a path after an `outputs` key does (`artifacts.verdict.content.done == true` is true when the artifact's file holds `{"done": true}`). The operand has no value, and never fails the run, when no step has published the artifact yet, it reads `content` of an artifact that is not one UTF-8 file of at most 65536 bytes, the content is not JSON under a path, or a path follows `revision`, `digest` or `dir`.

The list is evaluated just before the step or loop would start. A `when` inside `do` is evaluated again on every iteration, against the steps of the current iteration. When every expression is true the step runs normally. Otherwise it is skipped: it runs nothing and has no attempt, outputs, artifacts or step logs, a skipped `ask` shows no prompt and does not pause, and a skipped loop runs no iteration. A skip counts as successful, so a run whose remaining steps are all skipped ends `completed`. A step skipped before a run paused stays skipped when the run resumes.

The skip records the first failing expression, in the order written, and its cause:

| Cause | Meaning |
|---|---|
| `when_false` | The comparison that decided the expression resolved, and the expression was false. |
| `when_unresolved` | A step or artifact operand of the comparison that decided the expression had no value. |

!!! note
    A step operand has no value when its step was skipped or has not run, or when the step's result lacks the field or `outputs` key, such as a misspelled one; an `outputs` value that is JSON `null` is a value. An artifact operand has no value in the cases listed above, such as an artifact a later step publishes. A later comparison that references a skipped step is false, and a placeholder that references one interpolates to an empty string, as for a step that never ran.

!!! warning
    A step catalog file cannot set `when`: its step references can only be checked in the blueprint that uses it. Set `when` on the blueprint step that `uses` the catalog step instead.

## `if` Block

Runs its `then` steps when every `condition` is true, and its `else` steps otherwise. An `if` block sits only at the top level of `steps`.

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `id` | `string` | No | Derived | Unique identifier for the block. Derived like a top-level step id when omitted. |
| `type` | `string` | **Yes** | - | Must be `if`. |
| `name` | `string` | No | `null` | Display name shown when the block chooses its branch. |
| `condition` | `list[string]` | **Yes** | - | Conditions evaluated once, when the block starts, written as for `when`. At least one. |
| `then` | `list[step]` | **Yes** | - | Steps run in order when every condition is true. At least one. |
| `else` | `list[step]` | No | `null` | Steps run in order when a condition is false. At least one when set. |

The steps in `then` and `else` get the same shorthands as top-level steps, with derived ids `<block id>-then-step-<n>` and `<block id>-else-step-<n>`, and may carry `when`. `dovo blueprint validate`, `dovo run` and `dovo resume` reject a block, naming it, when `condition` or `then` is empty, `else` is set but empty, a branch holds a loop or another `if` block, a branch step id repeats any other step id, or `condition` references a step other than the steps before the block, a root other than `steps` or `artifacts`, or an artifact operand with no field or no step that publishes it. An `if` block inside a loop's `do` is rejected too.

The branch is chosen and saved when the block starts: `then`, `else`, or `none` when a condition is false and there is no `else`. A comparison that cannot be resolved is false. A run paused inside a branch resumes in that branch without evaluating the condition again. The block ends `completed` when every step of its branch completed, was ignored or was skipped, and otherwise takes the state of the first branch step that failed, paused or was cancelled. A block that ran nothing is `completed`.

A later step may reference the steps of either branch. A step of the branch that did not run resolves as a step that never ran: a condition on it is unresolved and a placeholder for it interpolates to an empty string.

## Agent step outcomes

An agent step records one outcome per attempt. A failed attempt's `error_code` is the only thing that tells why it failed, and its exit code is derived from that code.

| `error_code` | Dispatch | Code |
|---|---|---|
| none | `completed` | `0` |
| `AGENT_TASK_UNFIXABLE` | `failed` | `201` |
| `AGENT_TIMEOUT` | `failed` | `202` |
| `AGENT_TASK_BLOCKED` | `failed` | `204` |
| any other code | `failed` | `203` |

`AGENT_TASK_BLOCKED` means the provider refused a tool call under the step's `tools` policy and left the worktree unchanged. The error names the refused tool and says whether to grant the capability or to remove or narrow the matching `deny` rule. If the agent still made edits despite a refusal, the step continues as normal.

A direct-mutation agent produces `AGENT_TASK_UNFIXABLE` by writing `unfixable.txt` to its scratch directory (see [Private Scratch](../guides/agent-providers.md#private-scratch)).

Every other failure exits `203` with its own code: a provider failure records `AGENT_PROVIDER_FAILED`, an inactive worktree `STEP_WORKTREE_MISSING`, missing agent settings `STEP_AGENT_SETTINGS_MISSING`, a blank prompt `STEP_PROMPT_EMPTY`, a patch that fails to apply a `PATCH_*` or `GIT_FAILED` code, and an unexpected error or an output callback that raises `INTERNAL_ERROR`.

Stdout is always one JSON object followed by a newline. Its `status` is `proposed_patch` or `no_op` when the attempt completed and `null` when it failed:

```json
{"status":"no_op","summary":"Reviewed the changes; no edits were needed.","unfixable_reason":null,"touched_files":[]}
```

`summary` and `unfixable_reason` are `string | null`; `touched_files` is a sorted list of worktree-relative paths.

```text
steps.agent.outputs.status == "no_op"
steps.agent.error_code == "AGENT_TIMEOUT"
steps.agent.error_code == ""
steps.agent.exit_code == 202
```

- `no_op` succeeds with `0`. A planning or review step that finds nothing to change therefore completes, and `steps.<id>.outputs.status` is what tells `proposed_patch` from `no_op`.
- Branch on why an agent step failed with `steps.<id>.error_code`, which is `""` when the step recorded no error code. No agent output names a failure cause.
- The step also records the four summary fields as outputs, so `steps.<id>.outputs.status`, `summary`, `unfixable_reason` and `touched_files` read the same values in conditions and placeholders (see [Step Outputs](#step-outputs-dovo_output)). A `null` `status`, `summary` or `unfixable_reason` is recorded as an empty string, and `touched_files` as a JSON array string such as `["a.py", "b.py"]`. A `contains` condition on `touched_files` therefore matches text inside that string, so `contains "a.py"` also matches `ba.py`.
- These four outputs take precedence over a `$DOVO_OUTPUT` line with the same key: the step drops that line and records a warning naming the step and the key.
- These codes are step outcomes recorded per attempt. `dovo run` keeps its own exit contract, and command and script step exit codes are unchanged.

---

## Runtime Execution Metadata & Environment Variables

Steps receive run context through `DOVO_*` environment variables and template placeholders:

### Environment Variables

| Variable | Source Path | Description |
|---|---|---|
| `DOVO_STEP_ID` | `step.id` | Current step ID |
| `DOVO_STEP_NAME` | `step.name` | Current step display name |
| `DOVO_STEP_INDEX` | `step.index` | 1-based index of current step |
| `DOVO_STEP_ATTEMPT` | `step.attempt` | 1-based attempt count for this step execution |
| `DOVO_ITERATION_INDEX` | `iteration.index` | 1-based loop iteration index |
| `DOVO_BLUEPRINT_NAME` | `blueprint.name` | Name of running blueprint (or empty) |
| `DOVO_BLUEPRINT_SHA` | `blueprint.sha` | Blueprint catalog key (or empty) |
| `DOVO_PREVIOUS_STEP_ID` | `previous_step.id` | Step ID of immediately prior step (or empty) |
| `DOVO_PREVIOUS_STEP_NAME` | `previous_step.name` | Step name of immediately prior step (or empty) |
| `DOVO_PREVIOUS_STEP_INDEX` | `previous_step.index` | 1-based index of immediately prior step (or empty) |
| `DOVO_PREVIOUS_STEP_STATUS` | `previous_step.status` | Recorded status of immediately prior step (`completed`, `failed`, `ignored`, or empty) |
| `DOVO_PREVIOUS_STEP_EXIT_CODE` | `previous_step.exit_code` | Decimal exit code of immediately prior step (or empty) |
| `DOVO_STEPS_JSON` | `steps` | JSON array of finished step objects (`[{"id": "...", "name": "...", "index": "...", "status": "...", "exit_code": "..."}]`) — step outputs are never included here (see `outputs.<key>` interpolation below) |
| `DOVO_SESSION_ID` | `session_id` | ID of the session running the blueprint, the same for every step in the run (or empty when the run has no session) |
| `DOVO_TEMP` | `tmp.session_dir` | Session scratch directory, shared across all steps in the run (empty when no session ID is set) |
| `DOVO_RUNNER_TEMP` | `tmp.session_dir` | Alias for `DOVO_TEMP`, for GitHub Actions parity |
| `DOVO_STEP_TEMP` | `tmp.step_dir` | Step-specific scratch subdirectory under `DOVO_TEMP` |
| `DOVO_OUTPUT` | `tmp.output_file` | Path to append `key=value` (or heredoc) lines that become this step's `outputs` |

### Interpolation Paths

- **Current step**: `{{ step.id }}`, `{{ step.name }}`, `{{ step.index }}`, `{{ step.attempt }}`
- **Blueprint**: `{{ blueprint.name }}`, `{{ blueprint.sha }}`.- **Previous step**: `{{ previous_step.id }}`, `{{ previous_step.name }}`, `{{ previous_step.index }}`, `{{ previous_step.status }}`, `{{ previous_step.exit_code }}`
- **Historical steps (`steps`)**:
  - `{{ steps[0].<field> }}`: 0-based indexing for finished steps in run order.
  - `{{ steps[-1].<field> }}`: Python-style negative index (`-1` is the last finished step; matches `previous_step`).
  - `{{ steps.<id>.<field> }}` / `{{ steps['<id>'].<field> }}`: Keyed access by completed step ID.
  - Valid historical fields: `id`, `name`, `index` (1-based ordinal), `status`, `exit_code`.
  - `{{ steps.<id>.outputs.<key> }}` / `{{ steps['<id>'].outputs.<key> }}`: The value of output `<key>` of a completed step, as [Step Outputs](#step-outputs-dovo_output) defines it. An unknown step ID or output key resolves to an empty string.
  - The in-flight current step is never present in `steps`. Out-of-range indices or unknown step IDs safely resolve to empty strings.

### Step Outputs (`$DOVO_OUTPUT`)

A step can write `key=value` lines to the file at `$DOVO_OUTPUT` to expose values to later steps:

```bash
echo "greeting=hello" >> "$DOVO_OUTPUT"
```

Blank lines and lines starting with `#` are ignored. A line missing `=` is skipped with a warning rather than failing the step. `$DOVO_OUTPUT` is truncated before each attempt, so only the final attempt's writes are kept.

`steps.<id>.outputs.<key>` has one meaning in conditions and placeholders: the value the step recorded for `<key>` (written to `$DOVO_OUTPUT`, an ask step's answer, or an agent step's summary field), else the `<key>` of the step's stdout when that stdout is one JSON object. A recorded value wins over a stdout key of the same name. Stdout that is not a JSON object has no keys.

- In a placeholder, a stdout value renders the way recorded outputs hold values: a string as is, `null` as an empty string, a boolean as `true` or `false`, and a list or object as JSON. A dotted key such as `{{ steps.x.outputs.a.b }}` parses output `a` as JSON and reads its key `b`, as a condition does, and `{{ steps.x.outputs['a.b'] }}` reads the key `a.b`. A value that is not JSON or a missing path renders as an empty string.
- In a condition, `steps.<id>.outputs.<key>.<path>` parses the value as JSON and follows the path, so `steps.gate.outputs.report.score >= 80` reads `score` from a recorded `report` of `{"score": 85}`. A value that is not JSON or a missing path has no value. `steps.<id>.outputs` with no key has no value.
- In a condition, a string holding an integer or a decimal (`3`, `-2`, `2.5`) compares numerically against a number with every operator, so a recorded `count=3` passes `steps.gate.outputs.count > 2` and `steps.gate.outputs.count == 3`. Other strings, including `nan`, `1e3` and ` 3`, stay strings: `==` against a number is false, and `>`, `<`, `>=` or `<=` against a number is false.

For a multi-line value, use the heredoc form (mirrors GitHub Actions' `$GITHUB_OUTPUT`):

```bash
echo "body<<EOF_random_delimiter" >> "$DOVO_OUTPUT"
echo "line one" >> "$DOVO_OUTPUT"
echo "line two" >> "$DOVO_OUTPUT"
echo "EOF_random_delimiter" >> "$DOVO_OUTPUT"
```

Everything between the opening `key<<DELIM` line and the matching `DELIM` terminator line is captured verbatim (no `#`/blank-line/`=` handling applied inside the block) and joined with `\n`. Choose a delimiter unlikely to appear in the body itself, such as a random or UUID string — the parser treats any body line that exactly matches the delimiter as the terminator, with no escape mechanism. A heredoc missing its terminator drops the key and records a warning rather than failing the step.

### Precedence
1. Explicit step `env` key
2. `DOVO_*` metadata env
3. Ambient process env

Command and script steps inherit the ambient environment. Agent steps start from the filtered environment described in [Agent Providers](../guides/agent-providers.md#subprocess-environment): explicit `env` overrides passthrough and base values, but cannot override generated `DOVO_*` metadata, the adapter's controls, its credential names, or, for authored invocations, `DOVO_AGENT_SCRATCH`, `TMPDIR`, `TMP`, and `TEMP`.
