"""
orchestrator.py  —  STEP 2: Establish the closed loop

Request -> Validate -> Resolve:
    1. build_agent modifies target_code.py based on the spec.
    2. check.sh (deterministic) triggers instantly.
    3. If it fails, stderr/stdout is routed back to build_agent.
    4. Loop spins autonomously until check.sh returns exit code 0.

Notice this file is the ONLY place that knows both pieces exist.
build_agent.py doesn't know about check.sh. check.sh doesn't know about
build_agent.py. This is what makes them swappable — you could point this
same orchestrator at a "check_typescript.sh" and a "TSBuildAgent" without
touching either implementation, only the wiring here.
"""

import subprocess
from pathlib import Path

from build_agent import BuildAgent

TARGET_FILE = Path("target_code.py")
MAX_RETRIES = 4

SPEC = """
Write a Python function `add(a, b)` in a file called target_code.py.
- If both a and b are numbers, return their sum.
- If either a or b is a numeric string (like "5"), convert it and add anyway.
- If conversion is impossible, raise a ValueError.
Include type hints.
"""


def run_check() -> tuple[bool, str]:
    """Calls the deterministic gate as a black box. We don't care what's
    inside check.sh — only its exit code and its combined output."""
    result = subprocess.run(
        ["bash", "check.sh"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0, (result.stdout + result.stderr)


def main():
    agent = BuildAgent()

    # 1. Request: agent writes the first draft from the spec.
    print("--- Requesting initial build ---")
    TARGET_FILE.write_text(agent.write_code(SPEC))

    for attempt in range(1, MAX_RETRIES + 1):
        # 2. Validate: deterministic check, no human, no agent judgment.
        print(f"\n--- Attempt {attempt}: validating ---")
        passed, log = run_check()
        print(log)

        if passed:
            print(f"\n✅ PASSED on attempt {attempt}. Ready for human review.")
            return

        # 3. Resolve: route the exact failure back into the SAME agent
        #    session (build_agent keeps its own chat history internally).
        print(f"❌ Failed. Routing error log back to build agent...")
        TARGET_FILE.write_text(agent.fix_code(log))

    print(f"\n🚨 Gave up after {MAX_RETRIES} attempts. Escalating to human.")


if __name__ == "__main__":
    main()