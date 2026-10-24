An **Agent Expert Memory Engine** addresses one of the fundamental limitations of standard AI agents: **agents forget, which means agents do not learn**. A generic agent boots up, executes a task, and disappears—forcing you to pay the token cost of context exploration every single run.

In contrast, an **Agent Expert** is a specialized agent that **executes, learns, and updates its own working mental model at runtime** without human intervention.

---

### 1. The Core Primitive: The "Mental Model" File

In an Agent Expert system, every domain-specific worker maintains a **Mental Model** (typically a structured YAML or Markdown file located in `.claude/expertise/*.yaml`):

* **Working Memory vs. Source of Truth:** The source of truth is always the **executable code**. The Mental Model is not documentation or a PRD; it is a dynamic, evolving data structure that holds the agent's working memory of the codebase domain (key file locations, structural patterns, past edge cases, and known gotchas).
* **Instant Boot-Up:** Upon initialization, the sub-agent reads its dedicated `expertise` file. Because it already knows where key modules and dependencies reside, it bypasses costly file-search passes (`grep`, directory traversals), saving tens of thousands of tokens per task.
* **Superiority Over Global `CLAUDE.md` Files:** Global memory files force static, autoloaded context onto *every* task, polluting context windows and requiring manual human maintenance [1, 513–514]. Mental models are localized, updatable, and loaded only by the specific expert assigned to that domain.

---

### 2. The 3-Step Runtime Learning Loop (`self-improve`)

To teach an agent to build its own expertise over time, the harness embeds a closed-loop **`self-improve` protocol** into the agent's workflow:

```
┌────────────────────────────────────────────────────────────────────────┐
│ 1. Context Load                                                        │
│    Agent boots up and hot-loads its .claude/expertise/{domain}.yaml     │
├────────────────────────────────────────────────────────────────────────┤
│ 2. Task Execution & Validation                                         │
│    Agent executes file edits and passes deterministic test gates       │
├────────────────────────────────────────────────────────────────────────┤
│ 3. Self-Improvement Phase (Runtime Learning)                           │
│    Agent reviews git diff -> extracts new insights -> updates YAML     │
└────────────────────────────────────────────────────────────────────────┘
```

1. **Read Expertise:** The sub-agent loads its domain mental model before reading code.
2. **Execute & Validate:** The sub-agent performs edits and passes deterministic code gates (linters, test suites).
3. **Self-Reflect & Update:** Once tests pass, the harness invokes a `self-improve` skill. The sub-agent inspects the `git diff` of its work, distills new lessons or pattern shortcuts, and writes the updated insights back to `.claude/expertise/{domain}.yaml` [22, 25, 114–115].

---

### 3. Dual-Layer Memory Architecture

To prevent agents from hallucinating or overwriting critical engineering constraints, a mature memory engine combines two types of expertise [124–125]:

* **Static / Read-Only Expertise:** Inviolable business logic, security rules, or architectural invariants (e.g., PCI-DSS compliance constraints for a billing agent) that are hardcoded into system prompts or read-only files.
* **Dynamic / Updatable Expertise:** Evolving runtime observations (e.g., *"Websocket reconnects require flushing buffer X before triggering handler Y"*) managed and pruned autonomously by the agent [114–115, 185].

---

### 4. Compounding ROI Across Sessions

As your multi-agent factory runs hundreds of tickets, Agent Experts develop **stacking, compounding expertise**. Rather than relying on a single omni-agent that gets dumber as its context window fills, you orchestrate a team of hyper-specialized experts whose domain knowledge deepens with every merged PR.

---

