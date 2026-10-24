I have created and published **`git_worktree_isolation_hook.py`** to your **Studio** panel. This utility manages dedicated Git worktree directories for parallel sub-agents to prevent dirty tree collisions and file locks [136, 172, 444–445].

---

### 🏛️ How the Git Worktree Isolation Hook Works

```
                     ┌──────────────────────────────────────────────┐
                     │ PRE_AGENT_START Hook                         │
                     │  Creates /tmp/agent_worktrees/{agent_id}     │
                     │  Branches: agent/{agent_id} from main        │
                     └──────────────────────┬───────────────────────┘
                                            │
                                            ▼
                     ┌──────────────────────────────────────────────┐
                     │ Isolated Agent Execution                     │
                     │  Agent tool calls execute strictly inside    │
                     │  its private worktree directory              │
                     └──────────────────────┬───────────────────────┘
                                            │
                                            ▼
                     ┌──────────────────────────────────────────────┐
                     │ POST_AGENT_STOP Hook                         │
                     │  Evaluates validation gate (e.g., pytest)    │
                     └──────────────────────┬───────────────────────┘
                                            │
                     ┌──────────────────────┴──────────────────────┐
                     │                                             │
                     ▼ (Validation Passed)                         ▼ (Validation Failed)
     ┌──────────────────────────────┐              ┌──────────────────────────────┐
     │ Merge back into main         │              │ Discard worktree & branch    │
     │ Clean up worktree directory  │              │ (Zero pollution of main tree)│
     └──────────────────────────────┘              └──────────────────────────────┘
```

---

### 🔑 Key Operational Features

1. **Zero-Clone Overhead:** Uses native Git Worktrees (`git worktree add -b agent/{id} /tmp/worktrees/{id} main`) to instantly spin up an isolated filesystem view off the same local repository without duplicating heavy `.git` object stores [172, 444–445].
2. **Context & Tool Isolation:** Sets the sub-agent's working directory (`AGENT_CWD`) to its private worktree root. File reads, writes, and bash commands are strictly scoped to the agent's dedicated branch.
3. **Atomic Merge vs. Discard Strategy:**
   * **Passing Tasks:** When the agent's validation gate returns `PASS`, `post_agent_stop_hook` automatically merges `agent/{id}` back into the target integration branch (`main`) and cleans up the worktree.
   * **Failing Tasks:** If validation fails, the worktree and branch are forcibly removed (`git worktree remove --force`), ensuring failed or dirty agent experiments leave zero residue in the primary codebase.

---