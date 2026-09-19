# System Architecture: Autonomous Software Factory Router with E2B

This document defines the production-grade architectural blueprint for a **Factory Router** designed to parse inbound GitHub Issues, classify the work into specialized AI Developer Workflows (ADWs), spin up isolated E2B sandboxes, and orchestrate parallel coding agents.

---

## 1. High-Level Architectural Flow

```
[GitHub Issue/PR] 
       │ (Webhook Event)
       ▼
┌────────────────────────────────────────────────────────┐
│               Factory Router (FastAPI)                 │
│                                                        │
│ 1. Receives payload & validates webhook signature      │
│ 2. Extracts metadata (labels, body, branch)            │
│ 3. Classifies task (Feature, Hotfix, Chore)            │
└───────────────────────┬────────────────────────────────┘
                        │
                        ▼ (Spawns Thread/Task)
┌────────────────────────────────────────────────────────┐
│              E2B Sandbox Orchestrator                  │
│                                                        │
│ 1. Allocates secure, isolated E2B Sandbox              │
│ 2. Clones target Git repository branch                 │
│ 3. Injects Task Specification & Context                │
└───────────────────────┬────────────────────────────────┘
                        │
                        ▼
┌────────────────────────────────────────────────────────┐
│            Closed-Loop Agentic Execution               │
│                                                        │
│ 1. Builder Agent modifies codebase                     │
│ 2. Deterministic Validation Hooks execute (tests/lint) │
│ 3. Self-corrects on stderr errors (Validate & Resolve) │
└───────────────────────┬────────────────────────────────┘
                        │
                        ▼ (Success)
┌────────────────────────────────────────────────────────┐
│                 GitHub Handoff Layer                   │
│                                                        │
│ 1. Commits changes & pushes to origin branch           │
│ 2. Creates/Updates Pull Request following PR template  │
│ 3. Requests human review & destroys E2B Sandbox        │
└────────────────────────────────────────────────────────┘
```

---

## 2. Core Architectural Principles

### A. The Three Actors of Value Creation
Every automation step in this factory strategically delegates to the most efficient actor:
1. **The Engineer:** Shows up only at the endpoints (planning/requirements at the beginning, review/merging at the end) [11, 289].
2. **The Code:** Predictable, rapid, and token-free. Handles builds, linting, unit tests, and validation hooks [11, 107].
3. **The Agent:** Navigates non-deterministic code modifications and parses test failures into subsequent file edits [11, 107].

### B. Workspace Isolation
No agent runs directly on host production servers or local developer machines. By running agents inside **E2B sandboxes**, we guarantee absolute security [11, 1196, 1205]. If an agent executes `rm -rf /` or runs a malicious package script, it only damages a throwaway container that is destroyed minutes later.

### C. Class-Based Routing
Inbound tickets are classified by a lightweight LLM router to prevent wasting expensive, long-thinking reasoning models on simple chore tasks [10, 265, 274]:
*   **Chore Workflow:** Runs on lightweight models (e.g., Claude Haiku) for dependency updates or config adjustments [11, 18, 234].
*   **Feature Workflow:** deploys a multi-tier model stack. A frontier model (e.g., Claude Opus) drafts the implementation spec, and a workhorse model (e.g., Claude Sonnet) writes the code inside the E2B sandbox [10, 229, 221].
*   **Hotfix Workflow:** Prioritizes speed and triggers multiple parallel sandboxes running competing prompts or models; the first sandbox to successfully pass the validation suite is selected for the PR [11, 13].

---

## 3. Directory Layout for the E2B Sandbox

When E2B boots up your repository, it is configured to search for three standardized directories to immediately index codebase context [67, 1658]:

```
/workspace/
├── .claude/
│   ├── commands/     # Custom prompt templates/commands (e.g. /review, /build)
│   └── hooks/        # Deterministic lifecycle hooks (pre-tool, post-tool, stop)
├── ai_docs/          # Static, token-saving API & dependency documentation
├── specs/            # Injected GitHub Issue markdown file (The "Spec Plan")
└── src/              # Target codebase cloned at runtime
```

---

## 4. E2B Sandbox Closed-Loop Validation Pattern

The orchestrator enforces the **Request, Validate, and Resolve** pattern inside the sandbox [11, 177, 1315, 1402]:

1. **Step 1: Write Spec:** The orchestrator writes the GitHub issue body directly to `/workspace/specs/issue-spec.md` [15, 251, 375].
2. **Step 2: Read & Plan:** The coding agent reads `/workspace/specs/issue-spec.md` and uses `Plan Mode` to map files [29, 757].
3. **Step 3: Modify Code:** The agent executes target edits.
4. **Step 4: Post-Tool Hooks:** A deterministic post-tool hook instantly runs:
   ```bash
   pytest tests/
   ```
5. **Step 5: Autonomous Correction:** If the command fails, the output is fed back to the agent session:
   ```
   [System] Validation failed with Exit Code 1. Test logs:
   E   AssertionError: assert 'active' == 'inactive'
   Please resolve this regression.
   ```
6. **Step 6: Till-Done:** The agent continues loop cycles until all code constraints and tests pass.

---

## 5. E2B & FastAPI Implementation Plan

The accompanying `factory_router.py` file demonstrates:
1. **GitHub Webhook Handler:** A FastAPI router validating GitHub payload signatures using an HMAC key.
2. **E2B Sandbox Provisioning:** Creating a sandbox instance, running terminal commands to install dependencies, and cloning the codebase branch.
3. **Task Stacking & Execution:** Writing the spec file to E2B, running Claude Code on-the-fly, pushing changes, and cleaning up sandbox memory.
