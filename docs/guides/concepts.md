# Core Concepts & Mental Model

Dovo (`dovo`) is designed around a clean separation between **isolated worktrees**, **declarative blueprints**, and a **stateful runtime engine**.

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                           Dovo CLI (dovo)                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  dovo run <blueprint>                                                     │
│       │                                                                 │
│       ▼                                                                 │
│  ┌───────────────────────┐         ┌─────────────────────────────────┐  │
│  │ Blueprint Catalog     │         │ Git Worktree Manager             │  │
│  │ (.dovo/catalog/)  │         │                                 │  │
│  │  - blueprints/*.yml   │         │  Creates isolated worktree      │  │
│  │  - steps/*.yml        │         │  Branch: dovo/dovo_*     │  │
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

## 1. Ephemeral Git Worktrees

When you execute a blueprint, Dovo normally creates an isolated **Git worktree** using a dedicated `dovo/dovo_*` branch.

### Why Worktrees?
- **Zero Pollution**: Your active working directory and branch remain untouched while steps run.
- **Safety**: Broken code, test failures, or unintended edits are contained within the worktree.
- **Automatic Lifecycle**: Unless `--keep` is specified or execution is paused, the worktree and branch are automatically cleaned up when the run finishes.

If you ever want to run a blueprint directly in your current working directory without worktree isolation (e.g. in CI or a container), use the `--no-worktree` flag:

```bash
dovo run my-blueprint --no-worktree
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
| **Step** | Catalog item | A reusable shell-command, agent, or script definition. | `.dovo/catalog/steps/` |
| **Blueprint** | Catalog item | A sequence of steps with inputs, assertions, failure policies, and optional loop blocks. | `.dovo/catalog/blueprints/` |

---

## 3. The Blueprint Catalog

Blueprints live in your project's `.dovo/catalog/` directory:

```text
.dovo/catalog/
├── blueprints/         # Executable blueprints (e.g. fix-tests.yml)
└── steps/              # Reusable step definitions (e.g. run-tests.yml)
```

### Local Blueprints vs. Curated Templates
- **Local Blueprints**: Created and maintained within your repository for project-specific automation.
- **Curated Templates (`dovo/*`)**: Built-in catalog steps can be referenced using `uses: dovo/<name>`.

---

## 4. Execution Lifecycle & Sessions

Every execution via `dovo run` is tracked as a **Session**:

1. **Input Resolution**: Declared parameters and CLI flags are parsed and validated.
2. **Worktree Creation**: Ephemeral Git worktree branch is provisioned.
3. **Step Execution**: Steps run sequentially inside the worktree working directory.
4. **Assertions & Quality Gates**: Output and filesystem state are validated after each step.
5. **Resilience & Resumption**:
   - On error, `on_failure` policies determine whether to `abort`, `continue`, `retry`, or `prompt_user`.
   - If an interactive prompt is interrupted or paused, the run state is saved in the centralized database.
   - The session can be resumed at any time using `dovo resume blueprint_<id>`.
6. **Audit History**: All runs, durations, and outputs are recorded and accessible via `dovo history`.

---

## Next Steps

- Learn how to [Author Blueprints](authoring-blueprints.md).
- Dive into [Working with Steps](working-with-steps.md).
- Understand [Failure Handling and Session Resumption](failure-handling-and-resume.md).
