# Core Concepts & Mental Model

Worktree (`wt`) is designed around a clean separation between **isolated sandboxes**, **declarative blueprints**, and a **stateful runtime engine**.

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                           Worktree CLI (wt)                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  wt run <blueprint>                                                     │
│       │                                                                 │
│       ▼                                                                 │
│  ┌───────────────────────┐         ┌─────────────────────────────────┐  │
│  │ Blueprint Catalog     │         │ Git Sandbox Manager             │  │
│  │ (.worktree/catalog/)  │         │                                 │  │
│  │  - blueprints/*.yml   │         │  Creates isolated worktree      │  │
│  │  - steps/*.yml        │         │  Branch: worktree/sandbox-*     │  │
│  └───────────┬───────────┘         └────────────────┬────────────────┘  │
│              │                                      │                   │
│              └──────────────────┬───────────────────┘                   │
│                                 │                                       │
│                                 ▼                                       │
│                   ┌───────────────────────────┐                         │
│                   │ Runtime Engine & Observer │                         │
│                   │                           │                         │
│                   │  - Evaluates inputs       │                         │
│                   │  - Runs steps in sequence │                         │
│                   │  - Checks assertions      │                         │
│                   │  - Persists run state     │                         │
│                   │  - Records to SQLite DB   │                         │
│                   └───────────────────────────┘                         │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Ephemeral Git Sandboxes

When you execute a blueprint, Worktree normally creates an isolated **Git worktree** using a dedicated `worktree/sandbox-*` branch.

### Why Sandboxes?
- **Zero Pollution**: Your active working directory and branch remain untouched while steps run.
- **Safety**: Broken code, test failures, or unintended edits are contained within the sandbox.
- **Automatic Lifecycle**: Unless `--keep` is specified or execution is paused, the sandbox worktree and branch are automatically cleaned up when the run finishes.

If you ever want to run a blueprint directly in your current working directory without sandbox isolation (e.g. in CI or a container), use the `--no-sandbox` flag:

```bash
wt run my-blueprint --no-sandbox
```

---

## 2. Blueprints and Steps

A **Blueprint** is a declarative YAML document that defines a sequence of steps. It can contain ordinary steps and loop blocks.

```text
Blueprint
 ├── Step (command, agent, script, or reusable `uses:` reference)
 └── Loop block (repeats nested steps until its conditions pass)
```

| Concept | Type | Description | File Location |
|---|---|---|---|
| **Step** | Catalog item | A reusable shell-command, agent, or script definition. | `.worktree/catalog/steps/` |
| **Blueprint** | Catalog item | A sequence of steps with inputs, assertions, failure policies, and optional loop blocks. | `.worktree/catalog/blueprints/` |

---

## 3. The Blueprint Catalog

Blueprints live in your project's `.worktree/catalog/` directory:

```text
.worktree/catalog/
├── blueprints/         # Executable blueprints (e.g. fix-tests.yml)
└── steps/              # Reusable step definitions (e.g. run-tests.yml)
```

### Local Blueprints vs. Curated Templates
- **Local Blueprints**: Created and maintained within your repository for project-specific automation.
- **Curated Templates (`wt/*`)**: Built-in catalog steps can be referenced using `uses: wt/<name>`.

---

## 4. Execution Lifecycle & Sessions

Every execution via `wt run` is tracked as a **Session**:

1. **Input Resolution**: Declared parameters and CLI flags are parsed and validated.
2. **Sandbox Creation**: Ephemeral Git worktree branch is provisioned.
3. **Step Execution**: Steps run sequentially inside the sandbox working directory.
4. **Assertions & Quality Gates**: Output and filesystem state are validated after each step.
5. **Resilience & Resumption**:
   - On error, `on_failure` policies determine whether to `abort`, `continue`, `retry`, or `prompt_user`.
   - If an interactive prompt is interrupted or paused, the run state is saved in the centralized database.
   - The session can be resumed at any time using `wt resume blueprint_<id>`.
6. **Audit History**: All runs, durations, and outputs are recorded and accessible via `wt history`.

---

## Next Steps

- Learn how to [Author Blueprints](authoring-blueprints.md).
- Dive into [Working with Steps](working-with-steps.md).
- Understand [Failure Handling and Session Resumption](failure-handling-and-resume.md).
