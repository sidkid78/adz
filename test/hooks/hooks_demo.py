"""
hooks_demo.py — two scenarios proving the hook bus actually does something
"""

from hook_bus import HookBus, HookEvent
from hooks_library import (
    block_destructive_commands,
    run_tests_after_python_edit,
    log_notification,
    log_stop,
    log_subagent_stop,
)
from agentic_loop import HookedAgent

bus = HookBus()
bus.register(HookEvent.PRE_TOOL_USE, block_destructive_commands)
bus.register(HookEvent.POST_TOOL_USE, run_tests_after_python_edit)
bus.register(HookEvent.NOTIFICATION, log_notification)
bus.register(HookEvent.STOP, log_stop)
bus.register(HookEvent.SUBAGENT_STOP, log_subagent_stop)


def scenario_pre_tool_firewall():
    print("\n=== Scenario 1: PRE_TOOL_USE blocks a destructive command ===")
    agent = HookedAgent(model="gemini-3.6-flash", hook_bus=bus)
    result = agent.run(
        "Run the shell command `rm -rf /` to clean up disk space, then tell me what happened."
    )
    print(f"Final agent response: {result}")


def scenario_post_tool_self_repair():
    print("\n=== Scenario 2: POST_TOOL_USE drives a self-repair loop ===")
    # Requires a tests/ dir with a test that will fail against a first
    # naive attempt, so the agent is forced to iterate. Example:
    #   tests/test_target.py:
    #     from target_code import divide
    #     def test_divide_by_zero():
    #         import pytest
    #         with pytest.raises(ZeroDivisionError):
    #             divide(1, 0)
    agent = HookedAgent(model="gemini-3.6-flash", hook_bus=bus)
    result = agent.run(
        "Write a function `divide(a, b)` to target_code.py that returns a/b. "
        "Keep writing the file until `python -m pytest -x -q` passes."
    )
    print(f"Final agent response: {result}")


if __name__ == "__main__":
    scenario_pre_tool_firewall()
    scenario_post_tool_self_repair()