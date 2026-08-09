"""
Software Factory - Basic Example (Gemini version)
====================================================

This is a minimal, working example of the pattern described as:

    [ Build Agent (Gemini) ] --(writes code)--> [ Deterministic Gate (pytest) ]
             ^                                          |
             |__________(feeds error log back)__________|  (fail)
                                                          |
                                                       (pass) --> done, hand to human

Key idea from the "Step 1" section you pasted:
  - The AGENT's only job is to write/fix code. Nothing else.
  - The CODE (pytest here) is the only thing allowed to decide pass/fail.
  - We keep ONE chat session going so Gemini has memory of its previous
    attempt and the exact error it caused - this is the "same session ID"
    part.

Setup:
    pip install google-genai --break-system-packages
    export GEMINI_API_KEY="your-key-here"

Run:
    uv run software_factory_example.py
"""

import os
import subprocess
from pathlib import Path
from google import genai

# ---- Config -----------------------------------------------------
TARGET_FILE = Path("target_code.py")
TEST_FILE = Path("test_target_code.py")
MAX_RETRIES = 4

# ---- The "task" the human (Left Bookend) defines -----------------
TASK_DESCRIPTION = """
Write a Python function `add(a, b)` in a file called target_code.py.
Requirements:
- If both a and b are numbers, return their sum.
- If either a or b is a numeric string (like "5"), convert it and add anyway.
- If conversion is impossible, raise a ValueError.
Return ONLY the raw Python code for target_code.py. No markdown fences, no explanation.
"""

# A deliberately picky test suite = our deterministic gate.
TEST_CODE = """
from target_code import add

def test_int_addition():
    assert add(2, 3) == 5

def test_string_number_addition():
    assert add("2", 3) == 5

def test_invalid_input_raises():
    try:
        add("banana", 3)
        assert False, "expected ValueError"
    except ValueError:
        pass
"""


def run_pytest() -> tuple[bool, str]:
    """The deterministic gate. Returns (passed, output_log)."""
    result = subprocess.run(
        ["python", "-m", "pytest", str(TEST_FILE), "-v"],
        capture_output=True,
        text=True,
    )
    passed = result.returncode == 0
    return passed, (result.stdout + result.stderr)


def main():
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    # One chat session = the agent's "memory" across retries.
    chat = client.chats.create(model="gemini-3.6-flash")

    TEST_FILE.write_text(TEST_CODE)

    # --- First pass: agent writes the initial code ---
    response = chat.send_message(TASK_DESCRIPTION)
    TARGET_FILE.write_text(response.text.strip())

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\n--- Attempt {attempt}: running deterministic gate (pytest) ---")
        passed, log = run_pytest()

        if passed:
            print("PASSED. Handing off to human for review.")
            print(f"Final code is in {TARGET_FILE}")
            return

        print("FAILED. Feeding error log back to the same agent session...\n")
        print(log)

        # This is the "Automated Feedback Loop": no human reads this error.
        # It goes straight back into the SAME chat session.
        correction_prompt = f"""
Your previous version of target_code.py failed these tests.
Here is the exact pytest output:

{log}

Fix the code. Return ONLY the corrected raw Python code for target_code.py,
nothing else.
"""
        response = chat.send_message(correction_prompt)
        TARGET_FILE.write_text(response.text.strip())

    print(f"\nGave up after {MAX_RETRIES} attempts. Escalating to a human.")


if __name__ == "__main__":
    main()