I have created and published **`auto_compact_context_hook.py`** to your **Studio** panel. This Python script implements an **Auto-Compacting Context Hook** for your agent harness, designed to prevent model reasoning degradation as context windows approach saturation.

---

### 🏛️ How the Auto-Compacting Hook Operates

```
 ┌─────────────────────────────────────────────────────────┐
 │ 1. POST_TOOL_USE Hook Intercepts Tool Response           │
 └──────────────────────────┬──────────────────────────────┘
                            │
                            ▼
 ┌─────────────────────────────────────────────────────────┐
 │ 2. Evaluates Remaining Capacity                         │
 │    utilization = used_tokens / max_tokens               │
 └──────────────────────────┬──────────────────────────────┘
                            │
            ┌───────────────┴───────────────┐
            │                               │
            ▼ (capacity >= 15%)             ▼ (capacity < 15%)
    ┌───────────────┐             ┌──────────────────────────┐
    │ Allow Pass    │             │ 3. Execute R&D Compaction│
    │ (No action)   │             │    Extracts goals,       │
    └───────────────┘             │    modified files, and   │
                                  │    accomplished tasks    │
                                  └─────────────┬────────────┘
                                                │
                                                ▼
                                  ┌──────────────────────────┐
                                  │ 4. Re-inject State Anchor│
                                  │    Resets history to a   │
                                  │    single high-density   │
                                  │    summary block         │
                                  └──────────────────────────┘
```

---

### 🔑 Key Design Principles

1. **Capacity Monitoring (`used_tokens / max_tokens >= 0.85`):**
   The hook runs at `POST_TOOL_USE`. When context utilization exceeds 85% (less than 15% remaining capacity), it triggers compaction automatically before model responses start hallucinating or dropping earlier system instructions.

2. **The R&D Framework in Action (Reduce & Delegate):**
   Instead of keeping raw file content dumps, long tool logs, or repeated reasoning steps in memory, the compaction engine extracts four essential primitives:
   * **Original Goal:** What the user asked for.
   * **Modified Files:** Which files have touched disk so far (`git diff` footprint).
   * **Accomplished Steps:** Verified milestones and passing test assertions.
   * **Open Work:** What remains to be done.

3. **Fresh State Anchor Injection:**
   The hook replaces the bloated 50+ turn conversation history with a single, high-density **Summary Anchor**, resetting the agent's available context window back to near-100% capacity while preserving execution continuity.

---

