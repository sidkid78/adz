"""
factory_router.py  —  STEP 4: Deploy a Factory Router

A ticket comes in one of two ways:

  1. Fresh, with no complexity info -> classify_ticket() sorts it into
     CHORE / FEATURE / HOTFIX via a cheap classifier call.
  2. Pre-scoped, carrying source_complexity from an upstream system
     (see ingest_orchestrator_report.py) -> classify_ticket is SKIPPED
     entirely; COMPLEXITY_ROUTES trusts that system's own assessment
     instead of re-deriving one from scratch.

Either way, each route gets a different Gemini tier and a different
amount of machinery:

  CHORE / low complexity     -> gemini-3.5-flash-lite, no sandbox
  FEATURE / high complexity  -> gemini-3.1-pro plans, gemini-3.8-flash
                                 builds, sandboxed if the artifact is code
  medium complexity          -> gemini-3.8-flash builds directly, no
                                 separate planning pass
  HOTFIX                     -> gemini-3.8-flash, N attempts raced in
                                 parallel, first to pass the gate wins

Routing decides WHICH model and HOW MANY attempts. It never decides what
"passing" means — that comes from the ticket's own contract
(ticket_contracts.py) and is enforced by deterministic checks
(artifact_kinds.py). The builder is a specialist compiled from the
ticket's expertise domain and carrying that expert's accumulated lessons
(artifact_agent.py); the gate's verdict is what updates that memory.

Sandboxing is applied only where it buys something: a sandbox contains
code the gate EXECUTES. A markdown runbook or JSON field map is parsed,
never executed, so it is graded locally — see needs_sandbox().
"""

import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum
from pathlib import Path

from e2b import Sandbox
from google import genai

from artifact_agent import ArtifactBuildAgent, expert_for_ticket, system_prompt_for
from artifact_kinds import KINDS, run_checks
from ticket_contracts import ContractError, TicketContract, contract_for_ticket

REPO_ROOT = Path(__file__).resolve().parent


def default_contract() -> TicketContract:
    """The legacy add() contract, for the hand-written demo ticket.

    This used to be two module-level constants applied to EVERY ticket,
    which is what made the gate meaningless for anything that wasn't
    add(). It is now just a fallback, resolved lazily so importing this
    module from another directory doesn't blow up on a relative path."""
    return TicketContract(
        slug="target_code",
        kind="python_module",
        artifact_name="target_code",
        checks=[
            {"type": "ruff"},
            {"type": "mypy"},
            {"type": "pytest_suite"},
        ],
        test_code=(REPO_ROOT / "test_target_code.py").read_text(encoding="utf-8"),
    )


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
        "planning_model": "gemini-3.1-pro-preview",
        "build_model": "gemini-3.8-flash",
        "use_sandbox": True,
        "parallel_attempts": 1,
        "max_retries": 4,
    },
    TicketType.HOTFIX: {
        "build_model": "gemini-3.8-flash",
        "use_sandbox": True,
        "parallel_attempts": 3,  # race multiple sandboxes, first pass wins
        "max_retries": 2,        # each racer gets fewer tries — speed over polish
    },
}


# ---- Complexity-derived routes: for tickets that already carry a
# complexity assessment from an upstream system (see
# ingest_orchestrator_report.py) — skip classify_ticket entirely and
# trust the source system's own judgment instead of re-deriving it. ----
COMPLEXITY_ROUTES = {
    "high": {
        "planning_model": "gemini-3.1-pro-preview",
        "build_model": "gemini-3.8-flash",
        "use_sandbox": True,
        "parallel_attempts": 1,
        "max_retries": 4,
    },
    "medium": {
        # No planning_model: "medium" means real feature work, but not
        # complex enough to justify a separate reasoning-tier pass
        # before the build model touches it.
        "build_model": "gemini-3.8-flash",
        "use_sandbox": True,
        "parallel_attempts": 1,
        "max_retries": 3,
    },
    "low": ROUTES[TicketType.CHORE],  # reuse the existing chore route as-is
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
# Every executor rewrites the test file from the FROZEN contract before
# running, and never reads the check list from the workspace. That is
# what stops a build agent from editing its own gate: whatever it wrote
# over the tests is gone before they run, and the checks were never
# reachable from there in the first place.
def _seed_workspace(workspace: Path, content: str, contract: TicketContract) -> Path:
    artifact = workspace / contract.artifact_filename
    artifact.write_text(content, encoding="utf-8", newline="\n")
    if contract.test_filename and contract.test_code:
        (workspace / contract.test_filename).write_text(
            contract.test_code, encoding="utf-8", newline="\n"
        )
    return artifact


def run_gate_locally(content: str, contract: TicketContract) -> tuple[bool, str]:
    """Run the contract's checks in a throwaway workspace, so a build
    can never clobber checked-in files."""
    # ignore_cleanup_errors: ruff/mypy/pytest leave cache dirs behind and
    # Windows refuses to unlink them while handles are open. A stranded
    # temp dir must never fail a build that actually passed.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        workspace = Path(tmp)
        artifact = _seed_workspace(workspace, content, contract)
        return run_checks(artifact, contract.effective_checks())


def run_gate_in_sandbox(sbx: Sandbox, content: str, contract: TicketContract) -> tuple[bool, str]:
    """Same gate, executed inside e2b. Only worth paying for when the
    artifact is code that the checks will RUN (pytest imports the
    module). Document and config kinds are parsed, never executed, so
    they are graded locally — see needs_sandbox()."""
    remote = f"/home/user/{contract.artifact_filename}"
    sbx.files.write(remote, content)
    if contract.test_filename and contract.test_code:
        sbx.files.write(f"/home/user/{contract.test_filename}", contract.test_code)

    # Ship the check registry and contract into the sandbox and run the
    # same run_checks() there, so local and sandboxed verdicts come from
    # one implementation rather than two that can drift.
    sbx.files.write("/home/user/artifact_kinds.py", (REPO_ROOT / "artifact_kinds.py").read_text(encoding="utf-8"))
    sbx.files.write("/home/user/_gate.py", _SANDBOX_GATE_RUNNER)
    sbx.files.write("/home/user/_checks.json", json.dumps(contract.effective_checks()))
    sbx.commands.run("pip install ruff mypy pytest pyyaml -q")
    result = sbx.commands.run(
        f"python /home/user/_gate.py {contract.artifact_filename}", cwd="/home/user"
    )
    return result.exit_code == 0, result.stdout + result.stderr


_SANDBOX_GATE_RUNNER = """import json, sys
from pathlib import Path
from artifact_kinds import run_checks

checks = json.loads(Path("/home/user/_checks.json").read_text())
passed, log = run_checks(Path(sys.argv[1]), checks)
print(log)
sys.exit(0 if passed else 1)
"""


def needs_sandbox(contract: TicketContract, route_says_sandbox: bool) -> bool:
    """A sandbox contains code the gate EXECUTES. Only python_module has
    that property — its pytest stage imports and runs the artifact. A
    markdown runbook or a JSON field map is parsed, never executed, so
    sandboxing it buys no isolation and costs a container per attempt."""
    return route_says_sandbox and KINDS[contract.kind]["needs_test_code"]


# ---- 3. One "racer": a single attempt to solve the ticket ------------
def attempt_build(spec: str, build_model: str, use_sandbox: bool, max_retries: int,
                  contract: TicketContract, system_instruction: str):
    """One full build-validate-repair loop against ONE frozen contract.
    Returns the winning artifact contents, or None if it exhausts its
    retries.

    The agent never sees the checks. It learns what the contract wants
    only through the gate's rejection messages."""
    agent = ArtifactBuildAgent(
        model=build_model, contract=contract, system_instruction=system_instruction
    )
    content = agent.write(spec)

    sandboxed = needs_sandbox(contract, use_sandbox)
    sbx = Sandbox.create(timeout=300) if sandboxed else None
    try:
        for _ in range(max_retries):
            if sandboxed:
                passed, log = run_gate_in_sandbox(sbx, content, contract)
            else:
                passed, log = run_gate_locally(content, contract)

            if passed:
                return content

            content = agent.fix(log)
        return None
    finally:
        if sbx:
            sbx.kill()


# ---- 4. The router: dispatch based on ticket type ---------------------
def handle_ticket(ticket: dict, contract: TicketContract | None = None):
    source_complexity = ticket.get("source_complexity")

    if source_complexity in COMPLEXITY_ROUTES:
        # Trust the upstream system's own assessment — no classify_ticket
        # call, no re-derivation, no chance of it disagreeing with itself.
        route = COMPLEXITY_ROUTES[source_complexity]
        label = f"COMPLEXITY:{source_complexity.upper()} (from {ticket.get('source_subtask_id', 'source system')})"
        print(f"\n=== Ticket '{ticket['title']}' routed as {label} — skipped classify_ticket ===")
    else:
        ticket_type = classify_ticket(ticket)
        route = ROUTES[ticket_type]
        print(f"\n=== Ticket '{ticket['title']}' routed as {ticket_type.value.upper()} ===")

    print(f"Route config: {route}")

    # ---- Resolve the acceptance contract BEFORE any build agent exists.
    # A ticket whose deliverable isn't a file at all is a human
    # escalation, not something to paper over with invented checks.
    if contract is None:
        try:
            contract = contract_for_ticket(ticket)
        except ContractError as exc:
            print(f"--- NO CONTRACT: {exc}")
            print("--- ESCALATED to human (nothing was built) ---")
            return None
    print(f"Contract: {contract.describe()}")

    # ---- The specialist that will do the work. Its role comes from the
    # ticket's own source_expertise, so a dispatch-workflow ticket is
    # built by the dispatch expert, carrying every lesson that expert
    # has accumulated from previous dispatch tickets.
    expert = expert_for_ticket(ticket, contract.kind)
    system_instruction = system_prompt_for(expert, ticket, contract.kind)
    if expert is not None:
        e = expert.expertise
        print(f"Expert: {e.role} ({e.total_runs} prior runs, "
              f"{e.success_rate:.0%} success, {len(e.notes)} lessons carried in)")

    # A planning pass only happens if the route actually specifies one —
    # true for FEATURE and for complexity="high", false for everything else.
    spec = ticket["description"]
    if route.get("planning_model"):
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        plan = client.interactions.create(
            model=route["planning_model"],
            input=f"Write a precise implementation spec for: {spec}",
        )
        spec = plan.output_text.strip()
        print(f"--- {route['planning_model']} plan produced, handing to {route['build_model']} to build ---")

    def record(success: bool, output: str) -> None:
        """Feed the gate's verdict — never the agent's claim — back into
        the expert's persistent memory. This is the connection
        agent_expert.py's own __main__ flags as missing: 'success=True is
        hardcoded here for demo purposes... in a real pipeline this MUST
        come from an actual deterministic check.' The contract gate is
        that check. Same rule blueprints.py applies to expert steps."""
        if expert is None:
            return
        try:
            expert.record_outcome(task=ticket["title"], success=success, raw_output=output)
            print(f"Expert '{expert.expertise.role}' updated: "
                  f"{expert.expertise.total_runs} runs, "
                  f"{expert.expertise.success_rate:.0%} success")
        except Exception as exc:  # noqa: BLE001 - learning must never fail a build
            print(f"(expert memory not updated: {type(exc).__name__}: {exc})")

    n = route["parallel_attempts"]
    if n == 1:
        result = attempt_build(spec, route["build_model"], route["use_sandbox"],
                               route["max_retries"], contract, system_instruction)
        outcome = "PASSED" if result else "ESCALATED to human"
        print(f"--- {outcome} ---")
        record(bool(result), result or "gate never satisfied within retry budget")
        return result

    # HOTFIX path: race N sandboxes, take the first one that passes.
    # Every racer is graded against the SAME frozen contract — otherwise
    # "first to pass" would just mean "first to get the easiest tests".
    print(f"--- Racing {n} attempts in parallel ---")
    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [
            pool.submit(attempt_build, spec, route["build_model"], route["use_sandbox"],
                        route["max_retries"], contract, system_instruction)
            for _ in range(n)
        ]
        for future in as_completed(futures):
            result = future.result()
            if result:
                print("--- First successful build won the race. Cancelling the rest. ---")
                for f in futures:
                    f.cancel()  # best-effort; already-running ones finish naturally
                record(True, result)
                return result
    print("--- All racers failed. Escalating to human. ---")
    record(False, "all parallel racers exhausted their retries")
    return None


if __name__ == "__main__":
    # The hand-written demo ticket, which supplies the legacy add()
    # contract explicitly rather than paying to have one authored.
    example_ticket = {
        "title": "Prod: add() throwing on string input",
        "description": "add(a, b) crashes with TypeError when a or b is a numeric string like '5'. Needs to convert and add, or raise ValueError on truly invalid input.",
    }
    handle_ticket(example_ticket, contract=default_contract())

    # The ingested path — tickets carrying source_complexity from an
    # upstream report — now lives in run_factory.py:
    #     python run_factory.py specs/nfo.json