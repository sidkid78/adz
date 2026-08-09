"""
hooks_library.py — concrete hook implementations

These are ordinary Python functions with zero knowledge of Gemini or
the agent loop. Same isolation principle as check.sh from earlier
lessons: a hook could be tested by calling it directly with a fake
HookContext, no model required.
"""

import re
import subprocess

from hook_bus import HookContext, HookDecision

# ---- PRE_TOOL_USE: the firewall --------------------------------------
DESTRUCTIVE_PATTERNS = [
    re.compile(r"rm\s+-rf\s+/"),
    re.compile(r":\(\)\{.*:\|:.*\};:"),  # fork bomb
    re.compile(r">\s*/dev/sd"),
    re.compile(r"DROP\s+TABLE", re.IGNORECASE),
]


def block_destructive_commands(ctx: HookContext) -> HookDecision | None:
    if ctx.tool_name != "run_shell_command":
        return None
    command = (ctx.tool_args or {}).get("command", "")
    for pattern in DESTRUCTIVE_PATTERNS:
        if pattern.search(command):
            return HookDecision(block=True, reason=f"Matched destructive pattern: {pattern.pattern!r} in {command!r}")
    return None


# ---- POST_TOOL_USE: the engine ---------------------------------------
def run_tests_after_python_edit(ctx: HookContext) -> HookDecision | None:
    """After any write_file call touching a .py file, run pytest. On
    failure, the raw output becomes feedback the agent sees in its next
    turn — this IS the self-repair loop, no extra orchestration needed."""
    if ctx.tool_name != "write_file":
        return None
    path = (ctx.tool_args or {}).get("path", "")
    if not path.endswith(".py"):
        return None

    result = subprocess.run(
        ["python", "-m", "pytest", "-x", "-q"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return HookDecision(
            feedback=f"pytest FAILED after writing {path}:\n{result.stdout}{result.stderr}\n"
                      f"Fix the file and write it again."
        )
    return None


# ---- NOTIFICATION / STOP / SUBAGENT_STOP: logging, cost, TTS-ready ----
def log_notification(ctx: HookContext) -> HookDecision | None:
    print(f"[notify] {ctx.message}")
    return None


def log_stop(ctx: HookContext) -> HookDecision | None:
    print(f"[stop] Agent run finished.\n  final output: {ctx.message!r}\n  metadata: {ctx.metadata}")
    # A real system would trigger TTS or a system ping here instead of print().
    return None


def log_subagent_stop(ctx: HookContext) -> HookDecision | None:
    print(f"[subagent_stop] Nested agent finished.\n  result: {ctx.message!r}\n  metadata: {ctx.metadata}")
    return None


# ---- Sandbox-aware POST_TOOL_USE hook (for sandboxed_agent.py) --------
def make_sandbox_test_hook(sbx, cwd: str, test_command: str = "python -m pytest -x -q"):
    """Factory, not a plain function — this hook needs to know WHICH
    sandbox and WHICH worktree path to run tests in, so it closes over
    them instead of taking them from HookContext. Same trigger condition
    as run_tests_after_python_edit, just executed remotely via
    sbx.commands.run instead of local subprocess.run."""

    def hook(ctx: HookContext) -> HookDecision | None:
        if ctx.tool_name != "write_file":
            return None
        path = (ctx.tool_args or {}).get("path", "")
        if not path.endswith(".py"):
            return None

        result = sbx.commands.run(test_command, cwd=cwd)
        if result.exit_code != 0:
            return HookDecision(
                feedback=f"pytest FAILED in sandbox after writing {path}:\n{result.stdout}{result.stderr}\n"
                          f"Fix the file and write it again."
            )
        return None

    return hook