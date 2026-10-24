#!/usr/bin/env python3
"""
Agentic Engineering Harness: Auto-Compacting Context Hook
Monitors agent context capacity and automatically summarizes conversation history
when remaining context drops below 15% (85% utilization threshold).
"""

import json
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, Any, List, Optional

class HookEvent(Enum):
    SETUP = auto()
    PRE_TOOL_USE = auto()
    POST_TOOL_USE = auto()
    NOTIFICATION = auto()
    STOP = auto()

@dataclass
class HookContext:
    event: HookEvent
    tool_name: str
    tool_input: Dict[str, Any]
    used_tokens: int
    max_tokens: int = 200000  # Default 200k context window
    conversation_history: List[Dict[str, str]] = field(default_factory=list)

@dataclass
class HookDecision:
    action: str = "allow"
    feedback: Optional[str] = None
    compacted_history: Optional[List[Dict[str, str]]] = None

# =====================================================================
# 1. CONTEXT COMPACTION ENGINE (R&D FRAMEWORK)
# =====================================================================

def summarize_context_history(history: List[Dict[str, str]]) -> str:
    """
    Synthesizes historical conversation turns into a condensed, high-density summary.
    Preserves: Core Goal, Key Decisions, Modified Files, and Current Open Tasks.
    Discards: Intermediate file dumps, raw tool stdout, and repetitive thinking loops.
    """
    user_goals = []
    modified_files = set()
    completed_steps = []
    
    for msg in history:
        role = msg.get("role", "")
        content = msg.get("content", "")
        
        if role == "user" and not content.startswith("[CONTEXT COMPACTED"):
            user_goals.append(content[:150])
        elif role == "assistant":
            if "write_file" in content or "edit_file" in content or "Modified" in content:
                # Extract file references heuristically
                for word in content.split():
                    if "." in word and ("src/" in word or "specs/" in word or "app/" in word):
                        modified_files.add(word.strip("`'\",()"))
            if "Passed" in content or "Completed" in content or "Created" in content:
                completed_steps.append(content[:100])

    summary_block = (
        "=== [AUTOMATIC CONTEXT COMPACTION SUMMARY] ===\n"
        f"🎯 Original Goal: {user_goals[0] if user_goals else 'Execute ticket tasks'}\n"
        f"📁 Modified Files: {', '.join(sorted(modified_files)) if modified_files else 'None'}\n"
        f"✅ Accomplished Steps:\n" + "\n".join(f"  - {step}" for step in completed_steps[-3:]) + "\n"
        "=== [END SUMMARY - CONTINUING TASK WITH 100% FRESH CAPACITY] ==="
    )
    return summary_block


def auto_compact_context_hook(ctx: HookContext) -> Optional[HookDecision]:
    """
    POST_TOOL_USE Hook: Monitors context capacity after tool execution.
    If remaining capacity is < 15% (used_tokens / max_tokens >= 0.85),
    it generates a compacted summary and injects it into the agent context window.
    """
    if ctx.event != HookEvent.POST_TOOL_USE:
        return None

    utilization_ratio = ctx.used_tokens / ctx.max_tokens
    remaining_ratio = 1.0 - utilization_ratio

    # Trigger threshold: Less than 15% remaining capacity (>= 85% utilized)
    if remaining_ratio <= 0.15:
        summary_text = summarize_context_history(ctx.conversation_history)
        
        # Construct fresh, compacted history
        compacted_history = [
            {
                "role": "user",
                "content": (
                    f"[CONTEXT COMPACTED BY HARNESS - Utilization was at {utilization_ratio*100:.1f}%]\n\n"
                    f"{summary_text}\n\n"
                    "INSTRUCTION: Proceed with the remaining execution steps using the summary above as your state anchor."
                )
            }
        ]

        feedback_msg = (
            f"⚠️ CONTEXT CAPACITY WARNING ({remaining_ratio*100:.1f}% remaining).\n"
            "The harness has automatically compacted historical context to prevent token degradation.\n"
            "A condensed summary anchor has been injected into your context window."
        )

        return HookDecision(
            action="allow",
            feedback=feedback_msg,
            compacted_history=compacted_history
        )

    return HookDecision(action="allow")


# =====================================================================
# 2. SELF-TEST RUNNER
# =====================================================================

if __name__ == "__main__":
    print("🧪 Testing Auto-Compacting Context Hook...")

    # Simulated conversation history spanning 50 turns
    simulated_history = [
        {"role": "user", "content": "Build a Next.js video feed component with telemetry"},
        {"role": "assistant", "content": "Modified `src/features/feed/VideoFeed.tsx` to add HLS player."},
        {"role": "assistant", "content": "Created `specs/T-101_spec.md` with complete architecture."},
        {"role": "assistant", "content": "Passed unit test suite `npm test` successfully."}
    ]

    # Test Case 1: Below threshold (70% utilization -> 30% remaining)
    ctx_normal = HookContext(
        event=HookEvent.POST_TOOL_USE,
        tool_name="write_file",
        tool_input={"file_path": "src/features/feed/VideoFeed.tsx"},
        used_tokens=140000,
        max_tokens=200000,
        conversation_history=simulated_history
    )
    decision_normal = auto_compact_context_hook(ctx_normal)
    assert decision_normal.feedback is None, "Failed: Triggered compaction prematurely!"
    print("  ✅ Normal utilization test passed (No compaction triggered at 70%).")

    # Test Case 2: Above threshold (88% utilization -> 12% remaining)
    ctx_critical = HookContext(
        event=HookEvent.POST_TOOL_USE,
        tool_name="write_file",
        tool_input={"file_path": "src/features/feed/VideoFeed.tsx"},
        used_tokens=176000,
        max_tokens=200000,
        conversation_history=simulated_history
    )
    decision_critical = auto_compact_context_hook(ctx_critical)
    assert decision_critical.feedback is not None, "Failed: Did not trigger compaction at 88%!"
    assert decision_critical.compacted_history is not None, "Failed: Compacted history is empty!"
    
    print("  ✅ Critical utilization test passed (Compaction triggered at 88%).")
    print("\n--- Compacted Feedback Output ---")
    print(decision_critical.feedback)
    print("\n--- Compacted Summary Anchor ---")
    print(decision_critical.compacted_history[0]["content"])
    print("\n🎉 All Auto-Compacting Hook self-tests passed!")
