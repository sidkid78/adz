# Architecture Blueprint: Customizable Agent Harness
*A Technical Specification for High-Autonomy, Out-of-Loop Agent Orchestration*

The **Agent Harness** is the true product of agentic engineering [15, 608]. It serves as the dedicated runtime wrapper, security armor, and control loop that sits between raw foundation models, specialized worker agents, and your production codebase [15, 70, 427, 592]. Relying on default, opinionated third-party tools limits you to a normal distribution of results [69, 70, 80, 934]. To achieve exponential leverage, you must own and customize the agent harness [70, 100, 101, 105, 608].

This document outlines the core architecture of an enterprise-grade, customizable agent harness built around the **Core Four** primitives: **Context, Model, Prompt, and Tools** [15, 119, 184].

---

## 1. High-Level System Architecture

A robust agent harness decouples the non-deterministic reasoning of the language model from the deterministic constraints of software engineering [88, 110, 221]. It operates as a three-tier system:

```
            +---------------------------------------+
            |             Human Interface           |
            |        (CLI / Web UI / Slack)         |
            +-------------------+-------------------+
                                |
                                v
+-------------------------------+-----------------------------------+
|                         AGENT HARNESS                             |
|                                                                   |
|  +---------------------+  +--------------------+  +------------+  |
|  |   Prompt Registry   |  | Lifecycle Hook Bus |  |  Context   |  |
|  | (Slash Commands/Hops)|  | (Pre/Post/Stop/Sub)|  | Matcher    |  |
|  +----------+----------+  +---------+----------+  +-----+------+  |
|             |                       |                   |         |
|             +-----------+-----------+                   |         |
|                         |                               |         |
|                         v                               v         |
|                   +-----+-------------------------------+----+     |
|                   |               Harness Run Loop            |     |
|                   +---------------------+---------------------+     |
|                                         |                         |
+-----------------------------------------|-------------------------+
                                          | Programmatic Exec
                                          v
                            +-------------+-------------+
                            | Isolated Sandbox Router  |
                            | (EC2 / E2B Container Pool)|
                            +---------------------------+
```

---

## 2. Core Architectural Components

### A. The Prompt Registry (Slash Commands & Prompt Templates)
Prompts are the fundamental primitive of agentic compute [184, 747, 749, 1229]. The Prompt Registry acts as a centralized database of reusable, structured markdown templates (equivalent to custom slash commands) [26, 1224, 1227].

*   **Dynamic Variable Interpolation:** Supports binding static parameters (such as project rules) and runtime dynamic variables (user requests, directory targets) into positional arguments [1225, 1227, 1229].
*   **Higher-Order Prompts (Hops):** Treats prompts as code-like functions, enabling you to pass prompt templates as parameters inside other prompts (such as feeding a detailed specification plan into a parallelized build loop) [17, 18, 786, 1473].
*   **System Prompt Overwriting:** Allows the harness to completely strip away or selectively append to the model's global system prompt on boot, giving you total steerability over model personas (e.g., forcing a strict YAML response format) [26, 810, 818, 819].

### B. The Lifecycle Hook Bus (Deterministic Splicing)
Hooks allow you to inject predictable, deterministic code into the agent's non-deterministic execution cycle [164, 210, 221]. A customized harness registers callbacks across the **six essential lifecycle events** [164, 188, 205, 621, 622, 624, 1096]:

1.  **Setup Hook:** Executes heavy codebase preparation (dependency installation, migrations, pre-warming environments) once per session rather than on every turn [212, 213, 1096].
2.  **Pre-Tool Use Hook:** Acts as a security firewall. It intercepts tool requests (such as bash executions) *before* they run, checking commands against a blacklisted pattern library (e.g., blocking `rm -rf`) or launching an interactive human-in-the-loop "ask permission" block [412, 415, 418, 621, 623].
3.  **Post-Tool Use Hook:** Spliced immediately after a file modification or shell execution. It runs deterministic verification checks (Ruff linters, Pytest suites, Mypy type-checkers) and captures stderr outputs, feeding failures directly back into the agent's context window to establish an autonomous repair loop [54, 87, 88, 170, 175, 194, 1097, 1123, 1613].
4.  **Notification Hook:** Triggers a system alert or text-to-speech engine to ping the user whenever the background agent requires human input, has been blocked by permissions, or encounters a fatal crash [619, 620, 622, 1089, 1599].
5.  **Stop Hook:** Triggers when the primary agent completes its task. It compiles execution logs, calculates token costs, runs top-level system health validators, or audibly summarizes the run [180, 205, 622, 624, 1089].
6.  **Sub-Agent Stop Hook:** Fires when a spawned sub-agent concludes its isolated execution, allowing the primary agent to ingest the scoped results, release cached tokens, and cleanly tear down the sub-agent resources [622, 624, 1089].

### C. The Sandbox Connector (Isolated Dev Boxes)
For out-of-loop autonomy and damage control, agents must never run raw terminal commands directly on your local machine [84, 94, 426]. The Sandbox Connector manages a pooled registry of isolated container environments [71, 72, 84]:

*   **Pre-Warmed Dev Boxes:** Spins up pre-configured EC2 instances or E2B sandboxes in under 10 seconds, with the repository, system libraries, and database runtimes pre-loaded [84, 85, 86, 99, 530].
*   **Git Integration:** Operates on ephemeral branch worktrees, allowing multiple agents to run parallel lines of work in completely isolated environments, hedging against model failures [86, 1147, 1150, 1155].
*   **Resource Cleanup:** Automatically captures screenshots of visual updates, compiles execution diffs to `/workspace/out/`, and destroys the container post-run to ensure a zero-leftover state [362, 364, 539, 1201].

### D. The Context Matcher (Progressive Disclosure)
Stuffing a raw codebase of millions of lines of code into a single context window triggers context drift, token bloat, and model hallucination [114, 116, 119, 300, 305]. The Context Matcher implements **progressive disclosure** [309, 319, 320, 324]:

*   **Conditionally Scoped Rule Files:** Automatically attaches rules (MDC / `.cursorrules` patterns) based on the subdirectories the agent traverses [89, 114, 115, 116].
*   **Scout-Plan-Build Partitioning:** Leverages cheap, fast "scouter" models (like Haiku) to read file trees and locate target files, feeding only the relevant lines to the heavy "planning" model (Opus) to maximize context efficiency [449, 452, 1344, 1345, 1347].

---

## 3. The Harness Run Loop (The Core Engine)

The deterministic execution loop of the customized harness maps exactly to the **Request, Validate, and Resolve (RVR)** closed-loop prompt design [192, 195, 1392, 1613]:

```python
def run_session(user_request):
    # Step 1: Initialize Workspace (Setup Hook)
    trigger_hook("on_setup")
    
    # Step 2: Context Priming
    context = context_matcher.get_relevant_files(user_request)
    system_prompt = prompt_registry.get_system_prompt()
    
    # Step 3: Run Planning Phase (Restricted from Mutations)
    plan = model.generate_plan(user_request, context, system_prompt)
    
    # Step 4: Run Execution Phase (The Tool Loop)
    while not task_completed:
        tool_call = model.next_step()
        
        # Security Guard: Pre-Tool Hook
        if not trigger_hook("pre_tool_use", tool_call):
            log_and_notify("Action blocked by safety hook!")
            continue
            
        # Execute Action inside the Ephemeral Sandbox
        tool_output = sandbox.execute(tool_call)
        
        # Self-Validation: Post-Tool Hook
        validation_result = trigger_hook("post_tool_use", tool_output)
        if not validation_result.success:
            # Feed errors directly back to model for auto-repair
            model.feed_context(validation_result.errors)
            
    # Step 5: Tear Down and Summarize (Stop Hook)
    trigger_hook("on_stop")
```

---

## 4. Key Engineering Trade-offs

When designing your agent harness, always balance these trade-offs using the **Compute Advantage Equation** [416, 1416, 1422, 1427]:

| Parameter | Off-the-Shelf default (Cursor / Claude Code) | Forked & Customized (Stripe Minions / Pi Harness) |
| :--- | :--- | :--- |
| **Control Level** | Low (Opinionated defaults, black-box loops) [102, 592] | Absolute (Total control of the event loop & hooks) [100, 137] |
| **Autonomy** | In-loop only (Requires continuous human consent) [102, 103, 107] | Out-of-loop capable (Unattended, till-done pipelines) [66, 103, 104, 105] |
| **Context Window** | Volatile (Vulnerable to token-bloated MCP servers) [300, 305] | Highly efficient (Progressive disclosure, scouters) [309, 320, 328, 733] |
| **Safety Guard** | Dialog-box checks (Slowing down execution) [880, 910] | Hard-wired deterministic hooks & sandboxes [412, 426, 427, 918] |
