"""
factory_router.py  —  STEP 4: Deploy a Factory Router

A ticket comes in (imagine this triggered by a Kanban webhook). A cheap
classifier call sorts it into CHORE / FEATURE / HOTFIX, and each type
gets a different Gemini tier and a different amount of machinery:

  CHORE   -> gemini-3.5-flash-lite, no sandbox (cheap + low-risk, run
             locally like Step 2)
  FEATURE -> gemini-3.1-pro plans, gemini-3.6-flash builds, inside a
             sandbox (Step 3)
  HOTFIX  -> gemini-3.6-flash, N sandboxes in parallel, first one to
             pass check.sh wins

Everything downstream of routing reuses code you've already written:
check.sh and test_target_code.py are untouched. Only WHICH model and
HOW MANY sandboxes changes based on the route.
"""

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum
from pathlib import Path

from google import genai
from e2b import Sandbox

from gemini_build_agent import GeminiBuildAgent

CHECK_SCRIPT = Path("check.sh").read_text()
TEST_CODE = Path("test_target_code.py").read_text()


class TicketType(str, Enum):
    CHORE = "chore"
    FEATURE = "feature"
    HOTFIX = "hotfix"


# ---- The routing table: this is the whole point of Step 4 -----------
ROUTES = {
    TicketType.CHORE: {
        "build_model": "gemini-3.5-flash-lite",
        "use_sandbox": False,
        "parallel_attempts": 1,
        "max_retries": 2,
    },
    TicketType.FEATURE: {
        "planning_model": "gemini-3.1-pro",
        "build_model": "gemini-3.6-flash",
        "use_sandbox": True,
        "parallel_attempts": 1,
        "max_retries": 4,
    },
    TicketType.HOTFIX: {
        "build_model": "gemini-3.6-flash",
        "use_sandbox": True,
        "parallel_attempts": 3,  # race multiple sandboxes, first pass wins
        "max_retries": 2,        # each racer gets fewer tries — speed over polish
    },
}


# ---- 1. The Factory Router Agent: classify the incoming ticket ------
def classify_ticket(ticket: dict) -> TicketType:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    interaction = client.interactions.create(
        model="gemini-3.5-flash-lite",  # classification itself is a "chore" — cheapest tier
        input=(
            "Classify the ticket into exactly one word: chore, feature, or hotfix. "
            "hotfix = production is broken right now. "
            "feature = new capability, non-urgent, possibly large. "
            "chore = small routine maintenance, formatting, config, cleanup. "
            "Respond with only the single word.\n\n"
            f"Title: {ticket['title']}\nDescription: {ticket['description']}"
        ),
    )
    label = interaction.output_text.strip().lower()
    return TicketType(label)


# ---- 2. Execution primitives -----------------------------------------
def run_check_locally() -> tuple[bool, str]:
    result = subprocess.run(["bash", "check.sh"], capture_output=True, text=True)
    return result.returncode == 0, result.stdout + result.stderr


def run_check_in_sandbox(sbx: Sandbox, code: str) -> tuple[bool, str]:
    sbx.files.write("/home/user/target_code.py", code)
    sbx.files.write("/home/user/test_target_code.py", TEST_CODE)
    sbx.files.write("/home/user/check.sh", CHECK_SCRIPT)
    try:
        sbx.commands.run("pip install ruff mypy pytest -q")
        result = sbx.commands.run("bash /home/user/check.sh", cwd="/home/user")
        return result.exit_code == 0, result.stdout + result.stderr
    except Exception as e:
        stdout = getattr(e, "stdout", "")
        stderr = getattr(e, "stderr", "")
        log = f"{str(e)}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        return False, log


# ---- 3. One "racer": a single attempt to solve the ticket ------------
def attempt_fix(spec: str, build_model: str, use_sandbox: bool, max_retries: int):
    """Runs one full build-validate-resolve loop. Returns the winning
    code string on success, or None if it exhausts its retries."""
    agent = GeminiBuildAgent(model=build_model)
    code = agent.write_code(spec)

    sbx = Sandbox.create(timeout=300) if use_sandbox else None
    try:
        for _ in range(max_retries):
            if use_sandbox:
                passed, log = run_check_in_sandbox(sbx, code)
            else:
                Path("target_code.py").write_text(code)
                passed, log = run_check_locally()

            if passed:
                return sbx.files.read("/home/user/target_code.py") if use_sandbox else code

            code = agent.fix_code(log)
        return None
    finally:
        if sbx:
            sbx.kill()


# ---- 4. The router: dispatch based on ticket type ---------------------
def handle_ticket(ticket: dict):
    ticket_type = classify_ticket(ticket)
    route = ROUTES[ticket_type]
    print(f"\n=== Ticket '{ticket['title']}' routed as {ticket_type.value.upper()} ===")
    print(f"Route config: {route}")

    # FEATURE gets an extra planning pass with the reasoning-tier model
    # before the cheaper workhorse model ever touches code.
    spec = ticket["description"]
    if ticket_type == TicketType.FEATURE:
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        plan = client.interactions.create(
            model=route["planning_model"],
            input=f"Write a precise implementation spec for: {spec}",
        )
        spec = plan.output_text.strip()
        print(f"--- {route['planning_model']} plan produced, handing to {route['build_model']} to build ---")

    n = route["parallel_attempts"]
    if n == 1:
        result = attempt_fix(spec, route["build_model"], route["use_sandbox"], route["max_retries"])
        outcome = "PASSED" if result else "ESCALATED to human"
        print(f"--- {outcome} ---")
        return result

    # HOTFIX path: race N sandboxes, take the first one that passes.
    print(f"--- Racing {n} sandboxes in parallel ---")
    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [
            pool.submit(attempt_fix, spec, route["build_model"], route["use_sandbox"], route["max_retries"])
            for _ in range(n)
        ]
        for future in as_completed(futures):
            result = future.result()
            if result:
                print("--- First successful fix won the race. Cancelling the rest. ---")
                for f in futures:
                    f.cancel()  # best-effort; already-running ones finish naturally
                return result
    print("--- All racers failed. Escalating to human. ---")
    return None


if __name__ == "__main__":
    example_ticket = {
        "title": "Prod: add() throwing on string input",
        "description": "add(a, b) crashes with TypeError when a or b is a numeric string like '5'. Needs to convert and add, or raise ValueError on truly invalid input.",
    }
    handle_ticket(example_ticket)