# AI-Native Codebase Blueprint: Directories & Architectures for Agentic AI

This blueprint defines the optimal file system layouts, architectures, and directory patterns for building software alongside AI agents (such as Claude Code) [560, 564]. By designing your file system specifically for "AI Readability" rather than traditional layered structures, you minimize token waste, maintain tight focus, and enable autonomous, out-of-loop execution [579, 580].

---

## Part 1: The Three Essential Directories for Agentic AI

Every AI-native codebase should bootstrap three fundamental directories at the project root [1719]. These directories establish a clean separation between **knowledge** (AI Docs), **architecture/intent** (Specs), and **automation** (.claude) [1716, 1717, 1719].

```text
/your-project-root/
├── ai_docs/                 # Persistent AI Knowledge Base
│   ├── api-specs.md         # Documentation of internal API contracts
│   ├── library-docs.md      # Vendor or third-party documentation
│   └── architecture.md      # High-level architecture explanation
├── specs/                   # System Design & Implementation Specs
│   ├── sora-integration.md  # Detailed feature implementation specs
│   └── spec-template.md     # Standardized format for prompt specs
└── .claude/                 # Agent Automation and Control Configuration
    ├── commands/            # Custom slash commands (Prompt templates)
    │   ├── context-prime.md # Command to hot-load file context
    │   └── review-code.md   # Command to run multi-perspective reviews
    ├── skills/              # Specialized agent capabilities
    │   └── skill.md         # Custom capabilities script / documentation
    └── settings.json        # Global permissions, zero-access paths, and hooks
```

### 1. `ai_docs/` — The Persistent AI Knowledge Base
*   **Purpose:** Acts as a persistent, dedicated memory database for your AI agents [1716, 1721]. 
*   **Behavior:** Instead of pasting long API docs or README files into your chat sessions over and over (which consumes expensive token overhead), you store curated markdown files here [1716, 1721]. 
*   **Usage:** Agents are trained to read files from this directory to quickly ramp up on project libraries or API contracts [1715, 1721].

### 2. `specs/` — System Design & Implementation Specifications
*   **Purpose:** Holds detailed technical specifications, PRDs, and implementation plans [1716]. It is considered the most important folder in your entire codebase [1717].
*   **Behavior:** Grounded in the principle that **great planning is great prompting** [1717, 1725]. Before having an agent write code, you (or an architect agent) draft a strict, markdown-formatted spec file in this folder [1717, 1723]. 
*   **Usage:** You context-prime the agent with the spec, and the agent executes the blueprint in large, highly accurate swings without breaking existing features [1717, 1725].

### 3. `.claude/` (or `.claudcode/`) — Agent Automation & Configuration
*   **Purpose:** Houses all project-specific configuration, hooks, custom commands, and modular skills [205, 219, 222, 649].
*   **`commands/`:** Contains reusable prompt templates configured with dynamic arguments (e.g., `/context-prime [module]`) [205, 1284, 1289].
*   **`skills/`:** Contains modular files defining custom, agent-invokable capabilities (e.g., Playwright browser control or database query tools) [219, 768, 778, 1503].
*   **`settings.json`:** Standardizes agent permissions, blocks dangerous commands (e.g., blocking destructive shell commands), and declares lifecycle hooks (e.g., validating files post-execution) [427, 433, 440, 649].

---

## Part 2: AI-Optimized Vertical Slice Architecture

AI coding agents struggle with traditional **Layered Architectures** (e.g., separating controllers, services, and repositories into disparate, nested directories across the entire repository) because resolving cross-cutting changes requires loading too many unrelated files, causing token explosion [564, 579, 1419]. 

The **Vertical Slice Architecture** organizes codebases strictly by **features** rather than technical layers [566].

```text
/your-project-root/
└── src/
    └── features/               # Root of all vertical slices
        ├── projects/           # Co-located Project slice
        │   ├── projects-api.ts # API router/endpoints
        │   ├── projects-srv.ts # Business logic
        │   ├── projects-db.ts  # Database queries
        │   └── projects.test.ts# Domain unit/integration tests
        ├── tasks/              # Co-located Tasks slice
        │   ├── tasks-api.ts
        │   ├── tasks-srv.ts
        │   ├── tasks-db.ts
        │   └── tasks.test.ts
        └── users/              # Co-located Users slice
            ├── users-api.ts
            ├── users-srv.ts
            ├── users-db.ts
            └── users.test.ts
```

### Why This is Optimal for AI Agents
1.  **Instant Context Priming:** Because all database queries, business logic, API routers, and tests for a specific feature are co-located in a single directory, you can context-prime your agent with a single prompt (e.g., `read features/tasks/*`) [565, 567, 569]. This saves thousands of input tokens and cuts out "garbage context" [329, 569, 579].
2.  **Zero-Search Domain Boundaries:** An agent doesn't need to crawl your entire file system to locate the database schema or endpoint logic for a feature [5, 8]. It has everything contained in the vertical slice, which drastically minimizes hallucinations and bugs [565, 571].
3.  **Specialized Agent Experts:** It maps perfectly to "specialized agent experts" [573, 574]. You can lock a specialized developer agent to only read/write from a specific slice directory (e.g., a "tasks agent" only operates within `features/tasks/`), maximizing security and precision [573, 574].

---

## Part 3: Agentic Drop Zone (ADZ) Layout

The **Agentic Drop Zone (ADZ)** pattern steps outside of the chat interface entirely [342, 343]. It leverages the local file system as a reactive user interface [349, 354]. A local file-watcher daemon monitors these drop zones and automatically spins up headless agents to process documents the moment they are dropped [342, 345].

```text
/your-project-root/
└── drops/                      # Root Agentic Drop Zone Directory
    ├── config/
    │   └── drops.yaml          # Mapping configuration file
    ├── inbox/                  # User Drop Zones (Reactive Input)
    │   ├── finance/            # Drop folder for receipts & ledgers
    │   ├── specs/              # Drop folder for drafting codebases
    │   └── transcribers/       # Drop folder for audio recordings
    └── outbox/                 # Automated Agent Output
        ├── finance/            # Categorized CSVs & reports
        ├── specs/              # Oneshot-generated features & plans
        └── transcribers/       # Transcribed summaries & tasks
```

### Reactive Mechanics & Sample Configuration
A background watcher script (e.g., written in Python using `watchdog`) scans `drops/inbox/`. When a new file is detected, it loads the model and prompt template configured in `drops/config/drops.yaml` [342, 345].

#### Sample Configuration (`drops/config/drops.yaml`)
```yaml
# Drop Zone Reactive Configuration
directories:
  finance:
    inbox: "drops/inbox/finance"
    outbox: "drops/outbox/finance"
    model: "claude-3-7-sonnet"
    prompt: |
      You are a precise finance categorizer. Process the input file, extract 
      all expenses, run our standard validation script on the CSV, and 
      output a cleanly formatted categorized ledger to outbox/finance.
    validators:
      - "uv run validators/csv-single-validator.py"

  transcribers:
    inbox: "drops/inbox/transcribers"
    outbox: "drops/outbox/transcribers"
    model: "claude-3-5-haiku"
    prompt: |
      Analyze this raw transcript. Extract the key action items, create 
      reusable tasks, prioritize them, and output a markdown summary 
      to outbox/transcribers.
```

By leveraging this file system-driven setup, you can process high-leverage business pipelines—like sorting monthly finances, transcribing voice notes, or generating spec plans—purely via drag-and-drop file operations [343, 345, 354, 355].
