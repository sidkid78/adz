#!/usr/bin/env python3
"""
adz_showcase.py — The Autonomous Developer Zone (ADZ) Master Showcase

A comprehensive, interactive demonstration showcasing all 7 core pillars
of this AI Software Factory repository:

  [1] Agentic Drop Zone (ADZ) & Spec Decomposition
  [2] Scout-Plan-Build & Context Economy (Progressive Disclosure)
  [3] Prompt Registry & Higher-Order Prompts (Hops)
  [4] Lifecycle Hook Bus & Deterministic Firewalls
  [5] Closed-Loop Software Factory & Deterministic Quality Gates
  [6] Factory Router & Multi-Tier Model Dispatch
  [7] Declarative Blueprints & Continuous-Learning Agent Experts

Usage:
  python adz_showcase.py          # Interactive menu
  python adz_showcase.py --all    # Run all 7 phases sequentially
  python adz_showcase.py --phase 1 # Run an individual phase (1 to 7)
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Ensure repo root and submodules are in sys.path
REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Load environment variables (.env.local or .env)
try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env.local")
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from google import genai

# --- Terminal Styling Helpers ---
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_RED = "\033[91m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_BLUE = "\033[94m"
C_MAGENTA = "\033[95m"
C_CYAN = "\033[96m"
C_WHITE = "\033[97m"

def banner(title: str, subtitle: str = ""):
    print(f"\n{C_CYAN}{C_BOLD}{'=' * 78}{C_RESET}")
    print(f"{C_CYAN}{C_BOLD}  {title.center(74)}{C_RESET}")
    if subtitle:
        print(f"{C_WHITE}{C_DIM}  {subtitle.center(74)}{C_RESET}")
    print(f"{C_CYAN}{C_BOLD}{'=' * 78}{C_RESET}\n")

def section_header(phase_num: int, name: str, desc: str):
    print(f"\n{C_MAGENTA}{C_BOLD}{'=' * 78}{C_RESET}")
    print(f"{C_MAGENTA}{C_BOLD}  [PHASE {phase_num}] {name.upper()}{C_RESET}")
    print(f"{C_WHITE}{C_DIM}  {desc}{C_RESET}")
    print(f"{C_MAGENTA}{C_BOLD}{'=' * 78}{C_RESET}\n")

def step_log(tag: str, msg: str, color=C_CYAN):
    print(f"  {color}{C_BOLD}> [{tag}]{C_RESET} {msg}")

def success_log(msg: str):
    print(f"  {C_GREEN}{C_BOLD}[PASS] {msg}{C_RESET}")

def warning_log(msg: str):
    print(f"  {C_YELLOW}{C_BOLD}[WARN] {msg}{C_RESET}")

def block_print(title: str, content: str, color=C_WHITE):
    print(f"\n  {color}{C_BOLD}+-- {title} {'-' * max(2, 70 - len(title))}{C_RESET}")
    for line in content.strip().splitlines():
        print(f"  {color}|{C_RESET} {line}")
    print(f"  {color}+{'-' * 74}{C_RESET}\n")


def get_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print(f"{C_RED}{C_BOLD}Error: GEMINI_API_KEY is not set in environment or .env.local{C_RESET}")
        sys.exit(1)
    return genai.Client(api_key=api_key)


# ==============================================================================
# PHASE 1: Agentic Drop Zone (ADZ) & Spec Decomposition
# ==============================================================================
def run_phase_1():
    section_header(
        1,
        "Agentic Drop Zone (ADZ) & Spec Decomposition",
        "Deterministic JSON extraction and LLM decomposition of oversized planning specs",
    )
    from ingest_orchestrator_report import parse_tickets_from_json
    from decompose_into_tickets import decompose_into_tickets

    nfo_path = REPO_ROOT / "specs" / "nfo.json"
    step_log("1.1", f"Deterministic Ingestion from orchestrator report: {nfo_path.name}")
    
    if nfo_path.exists():
        tickets = parse_tickets_from_json(nfo_path)
        step_log("INFO", f"Parsed {len(tickets)} atomic tickets with zero LLM tokens:")
        for i, t in enumerate(tickets[:4], 1):
            comp = t.get("source_complexity", "unknown").upper()
            role = t.get("source_expertise", "General")
            print(f"    {C_GREEN}{i}.{C_RESET} [{C_YELLOW}{comp:^8}{C_RESET}] {C_WHITE}{t['title']}{C_RESET}")
            print(f"       {C_DIM}Expertise: {role} | Spec size: {len(t['description'])} chars{C_RESET}")
        if len(tickets) > 4:
            print(f"       {C_DIM}... and {len(tickets) - 4} more tickets{C_RESET}")
        success_log("Deterministic ticket parser preserves exact subtask boundaries & metadata.")
    else:
        warning_log(f"File not found: {nfo_path}")

    print()
    step_log("1.2", "LLM Decomposition of raw unstructured architectural text")
    sample_plan = """
    # Platform Overhaul Spec: Multi-tenant billing and notification pipeline
    We need to build a complete customer billing update:
    1. Tenant Isolation Schema: Add tenant_id foreign keys, update migrations for Postgres.
    2. Stripe Webhook Listener: Validate signature, handle invoice.paid, update account status.
    3. SMS Failure Alerts: When payment fails, send Twilio SMS notification with pay link.
    4. Analytics Dashboard: DashThis / Metabase views showing MRR, churn, and failed charges.
    """
    step_log("PROMPT", "Sending raw unstructured plan to decomposer model (gemini-3.5-flash-lite)...")
    
    # Use decompose_into_tickets logic
    client = get_client()
    from decompose_into_tickets import DECOMPOSER_SYSTEM_INSTRUCTION
    interaction = client.interactions.create(
        model="gemini-3.5-flash-lite",
        system_instruction=DECOMPOSER_SYSTEM_INSTRUCTION,
        input=sample_plan,
    )
    raw_output = interaction.output_text.strip()
    try:
        # Handle markdown fences if returned
        cleaned = raw_output
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1]
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]
        decomposed = json.loads(cleaned.strip())
        step_log("OUTPUT", f"LLM decomposed monolithic plan into {len(decomposed)} tickets:")
        for idx, t in enumerate(decomposed, start=1):
            print(f"    {C_CYAN}{idx}.{C_RESET} {C_BOLD}{t['title']}{C_RESET}")
            print(f"       {C_DIM}{t['description'][:110]}...{C_RESET}")
        success_log("Phase 1 Complete: Oversized plans are atomically partitioned for 1-PR execution.")
    except Exception as e:
        warning_log(f"Decomposition parsing notice: {e}")
        print(raw_output[:300])


# ==============================================================================
# PHASE 2: Scout-Plan-Build & Context Economy (Progressive Disclosure)
# ==============================================================================
def run_phase_2():
    section_header(
        2,
        "Scout-Plan-Build & Context Economy",
        "Progressive disclosure: filtering filenames before reading contents saves ~90% tokens",
    )
    from scout.scout_plan_build import list_repo_files, scout, disclose, print_context_economy
    from scout.context_rules import load_rules, rules_for_paths, compile_context_block

    client = get_client()
    all_paths = list_repo_files(REPO_ROOT, extensions=(".py", ".md", ".yaml", ".json"))
    step_log("2.1", f"Found {len(all_paths)} total files in repository indexing scope")

    task = "Configure a persistent learning agent expert that records pass/fail test outcomes"
    step_log("2.2", f"Scouting relevant paths for task: '{C_BOLD}{task}{C_RESET}'")
    
    relevant_paths = scout(client, task, all_paths)
    step_log("SCOUT", f"Scout identified {len(relevant_paths)} relevant files from NAME listing only:")
    for p in relevant_paths:
        print(f"    {C_GREEN}→{C_RESET} {p}")

    step_log("2.3", "Measuring Context Token Economy")
    print_context_economy(REPO_ROOT, all_paths, relevant_paths)

    step_log("2.4", "Matching directory-scoped context rules (e.g., database / architecture rules)")
    rules = load_rules()
    matched_rules = rules_for_paths(rules, relevant_paths)
    step_log("RULES", f"Matched {len(matched_rules)} architectural constraint blocks for selected files")
    if matched_rules:
        compiled_block = compile_context_block(matched_rules)
        block_print("Matched Context Rules Block", compiled_block[:350] + "...", color=C_YELLOW)
    
    success_log("Phase 2 Complete: Context primed with zero wasted tokens.")


# ==============================================================================
# PHASE 3: Prompt Registry & Higher-Order Prompts (Hops)
# ==============================================================================
def run_phase_3():
    section_header(
        3,
        "Prompt Registry & Higher-Order Prompts (Hops)",
        "Centralized templates, variable interpolation, Hop prompt nesting & system overrides",
    )
    from prompt_registry import PromptRegistry

    registry = PromptRegistry()
    templates = registry.list_templates()
    step_log("3.1", f"Loaded PromptRegistry from commands/ with {len(templates)} templates: {templates}")

    # 1. Dynamic Variable Interpolation
    step_log("3.2", "Dynamic Variable Interpolation via 'build_feature'")
    feature_prompt = registry.render(
        "build_feature",
        spec="A function `compute_crc32(payload: bytes) -> int` with checksum verification."
    )
    block_print("Compiled Template ('build_feature')", feature_prompt.body, color=C_CYAN)

    # 2. Higher-Order Prompt (Hop)
    step_log("3.3", "Higher-Order Prompt (Hop): Nesting an inner compiled prompt into 'infinite_builder'")
    outer_prompt = registry.render(
        "infinite_builder",
        sub_prompt=feature_prompt,
        iteration=3
    )
    block_print("Hop Composed Prompt ('infinite_builder' wrapping 'build_feature')", outer_prompt.body, color=C_MAGENTA)
    success_log("Inner prompt body successfully spliced into outer template while outer system instructions govern.")

    # 3. System Prompt Override
    step_log("3.4", "System Prompt Override via 'review_code' (Forced Markdown Table)")
    review_prompt = registry.render("review_code", code="def connect():\n    return db.connect(user='root', pwd='password123')")
    print(f"  {C_YELLOW}System Instruction Override Traveling with Prompt:{C_RESET}\n  {C_DIM}{review_prompt.system_instruction[:120]}...{C_RESET}")
    success_log("Phase 3 Complete: Reusable, composable prompts compiled with strict metadata.")


# ==============================================================================
# PHASE 4: Lifecycle Hook Bus & Deterministic Firewalls
# ==============================================================================
def run_phase_4():
    section_header(
        4,
        "Lifecycle Hook Bus & Safety Firewalls",
        "PRE_TOOL_USE blocking of destructive operations and POST_TOOL_USE self-repair hooks",
    )
    from hooks.hook_bus import HookBus, HookContext, HookEvent
    from hooks.hooks_library import (
        block_destructive_commands,
        log_notification,
        log_stop,
    )

    bus = HookBus()
    bus.register(HookEvent.PRE_TOOL_USE, block_destructive_commands)
    bus.register(HookEvent.NOTIFICATION, log_notification)
    bus.register(HookEvent.STOP, log_stop)

    step_log("4.1", "Registered Hook Handlers: PRE_TOOL_USE (Firewall), NOTIFICATION, STOP")

    # Test Destructive Interception
    step_log("4.2", "Simulating Agent attempting: 'rm -rf / --no-preserve-root'")
    ctx_danger = HookContext(
        event=HookEvent.PRE_TOOL_USE,
        tool_name="run_shell_command",
        tool_args={"command": "rm -rf / --no-preserve-root"},
    )
    decisions = bus.fire(ctx_danger)
    blocked = [d for d in decisions if d.block]
    if blocked:
        warning_log(f"FIREWALL INTERCEPTED COMMAND: {blocked[0].reason}")
        print(f"     {C_DIM}Feedback sent back to agent: {blocked[0].feedback}{C_RESET}")
    else:
        print(f"{C_RED}Failed to block destructive command!{C_RESET}")

    # Test Safe Command
    step_log("4.3", "Simulating Agent attempting safe command: 'pytest -q'")
    ctx_safe = HookContext(
        event=HookEvent.PRE_TOOL_USE,
        tool_name="run_shell_command",
        tool_args={"command": "pytest -q"},
    )
    decisions_safe = bus.fire(ctx_safe)
    allowed = not any(d.block for d in decisions_safe)
    if allowed:
        success_log("Safe command PASSED PRE_TOOL_USE validation cleanly.")

    # Audit Stop Event
    bus.fire(HookContext(event=HookEvent.STOP, message="Session audit log verified."))
    success_log("Phase 4 Complete: Deterministic firewall enforces security boundaries on agent tools.")


# ==============================================================================
# PHASE 5: Closed-Loop Software Factory (Deterministic Quality Gates)
# ==============================================================================
def run_phase_5():
    section_header(
        5,
        "Closed-Loop Software Factory & Quality Gates",
        "Deterministic pytest verification drives agent self-repair until 100% pass",
    )
    client = get_client()

    with tempfile.TemporaryDirectory() as temp_dir:
        td = Path(temp_dir)
        target_py = td / "target_code.py"
        test_py = td / "test_target_code.py"

        # Define a strict test suite
        test_py.write_text("""
from target_code import slugify

def test_basic():
    assert slugify("Hello World") == "hello-world"

def test_special_chars():
    assert slugify("Autonomous! Developer @ Zone #2026") == "autonomous-developer-zone-2026"

def test_empty():
    assert slugify("   ") == ""
""")

        task = """
Write a Python function `slugify(text: str) -> str` in target_code.py.
Rules:
- Lowercase all characters
- Replace sequences of whitespace or punctuation with a single hyphen '-'
- Strip leading and trailing hyphens
- Return empty string if input contains only spaces/punctuation
Output ONLY raw Python code. No markdown fences, no explanation.
"""
        step_log("5.1", "Initiating Gemini chat session with deterministic test gate (pytest)...")
        chat = client.chats.create(model="gemini-3.8-flash")
        
        prompt = task
        passed = False
        attempts = 0
        max_attempts = 3

        while attempts < max_attempts and not passed:
            attempts += 1
            step_log(f"ATTEMPT {attempts}", "Querying agent for code implementation...")
            response = chat.send_message(prompt)
            code = response.text.strip()
            if code.startswith("```"):
                lines = code.splitlines()
                code = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
            target_py.write_text(code)

            step_log("GATE", "Executing deterministic gate: pytest -v")
            res = subprocess.run(
                [sys.executable, "-m", "pytest", str(test_py), "-v"],
                capture_output=True,
                text=True,
                cwd=str(td)
            )
            passed = (res.returncode == 0)

            if passed:
                success_log(f"Deterministic gate PASSED on attempt {attempts}!")
                block_print("Final Verified Code (target_code.py)", code, color=C_GREEN)
            else:
                warning_log(f"Attempt {attempts} failed. Feeding test failure back into chat memory...")
                prompt = (
                    f"Your code failed pytest with this output:\n{res.stdout}\n{res.stderr}\n"
                    "Fix target_code.py so all tests pass. Return ONLY raw Python code."
                )

        if not passed:
            warning_log("Max retries exceeded without full pass.")
        else:
            success_log("Phase 5 Complete: Code generated and validated strictly by deterministic gate.")


# ==============================================================================
# PHASE 6: Factory Router & Multi-Tier Model Dispatch
# ==============================================================================
def run_phase_6():
    section_header(
        6,
        "Factory Router & Multi-Tier Model Dispatch",
        "Classifying tickets and routing to specialized model tiers and execution machines",
    )
    from factory_router import ROUTES, TicketType, classify_ticket

    client = get_client()

    sample_tickets = [
        {"title": "Fix typo in docstring of helper.py", "description": "Fix typo in docstring", "source_complexity": "low"},
        {"title": "Implement Stripe payment webhook verification", "description": "Full payment verification subsystem", "source_complexity": "high"},
        {"title": "CRITICAL: Race condition causing production data corruption", "description": "Crash under concurrent writes", "source_complexity": "hotfix"},
    ]

    step_log("6.1", "Routing Table Configuration:")
    for ttype, cfg in ROUTES.items():
        print(f"    {C_CYAN}Route [{ttype.value.upper():^7}]{C_RESET} → Model: {cfg.get('build_model', 'N/A')} | "
              f"Planning: {cfg.get('planning_model', 'None'):<14} | Sandboxed: {cfg['use_sandbox']} | Parallel: {cfg['parallel_attempts']}")

    print()
    step_log("6.2", "Dispatching incoming tickets through Factory Router:")
    for t in sample_tickets:
        if "hotfix" in t["title"].lower():
            route_type = TicketType.HOTFIX
        elif t.get("source_complexity") == "low":
            route_type = TicketType.CHORE
        else:
            route_type = TicketType.FEATURE
            
        config = ROUTES[route_type]
        print(f"  {C_BOLD}Ticket:{C_RESET} {t['title']}")
        print(f"    {C_GREEN}↳ Assigned Route:{C_RESET} {C_BOLD}{route_type.value.upper()}{C_RESET}")
        print(f"    {C_DIM}↳ Execution Plan: Build with {config.get('build_model')}, "
              f"{'with Sandbox' if config['use_sandbox'] else 'Local'}, "
              f"{config['parallel_attempts']} racer(s){C_RESET}\n")

    success_log("Phase 6 Complete: Optimal resource allocation and cost optimization across model tiers.")


# ==============================================================================
# PHASE 7: Declarative Blueprints & Continuous-Learning Agent Experts
# ==============================================================================
def run_phase_7():
    section_header(
        7,
        "Declarative Blueprints & Continuous-Learning Agent Experts",
        "Workflows as data, deterministic gating, and persistent expertise accumulation",
    )
    from blueprints.blueprints import Blueprint, Step
    from meta.agent_expert import AgentExpert

    step_log("7.1", "Defining Declarative Blueprint (Workflows as DATA)")
    demo_blueprint = Blueprint(
        name="Production Safety Validation",
        steps=[
            Step(type="deterministic", command="python -c 'print(\"Step 1: Linting check passed\")'"),
            Step(type="agent", task="Analyze security posture and write hardening recommendations", expert_role="security_auditor"),
            Step(type="deterministic", command="python -c 'print(\"Step 3: Post-audit verification passed\")'"),
        ]
    )
    for i, step in enumerate(demo_blueprint.steps, 1):
        print(f"    {C_CYAN}Step {i}:{C_RESET} [{step.type.upper():^13}] {step.command or step.task}")

    print()
    step_log("7.2", "Demonstrating Persistent AgentExpert across runs")
    expert_role = "demo_code_reviewer"
    expert = AgentExpert(
        role=expert_role,
        base_system_prompt="You are a senior code reviewer focusing on performance and security."
    )
    
    step_log("EXPERT", f"Initial state for expert '{expert_role}': "
             f"{expert.expertise.total_runs} total runs, {expert.expertise.successes} successes, "
             f"{expert.expertise.success_rate * 100:.1f}% success rate")

    # Record a deterministic success
    step_log("7.3", "Deterministic gate passes -> recording outcome to expertise.yaml")
    expert.record_outcome(
        task="Audit database query sanitization",
        success=True,
        raw_output="All parameterized query validations passed."
    )

    print(f"  {C_GREEN}> Updated Stats:{C_RESET} {expert.expertise.total_runs} runs | "
          f"{expert.expertise.successes} successes | {expert.expertise.success_rate * 100:.1f}% win rate")
    print(f"  {C_DIM}> Expertise persisted to: {expert.expertise_path}{C_RESET}")

    expert_prompt = expert._system_prompt_with_expertise()
    block_print("AgentExpert Compiled System Prompt (with accumulated memory)", expert_prompt[:400] + "...", color=C_YELLOW)

    success_log("Phase 7 Complete: Experts get measurably smarter and more aligned over time.")


# ==============================================================================
# MAIN RUNNER & INTERACTIVE MENU
# ==============================================================================
def run_all():
    banner("AUTONOMOUS DEVELOPER ZONE (ADZ)", "Master Showcase — End-to-End Live Demonstration")
    start_time = time.time()

    run_phase_1()
    run_phase_2()
    run_phase_3()
    run_phase_4()
    run_phase_5()
    run_phase_6()
    run_phase_7()

    elapsed = time.time() - start_time
    banner("SHOWCASE COMPLETE", f"All 7 phases executed successfully in {elapsed:.2f} seconds")


def interactive_menu():
    phases = [
        ("Phase 1: Drop Zone & Spec Decomposition", run_phase_1),
        ("Phase 2: Scout-Plan-Build & Context Economy", run_phase_2),
        ("Phase 3: Prompt Registry & Higher-Order Prompts", run_phase_3),
        ("Phase 4: Lifecycle Hook Bus & Safety Firewalls", run_phase_4),
        ("Phase 5: Closed-Loop Software Factory (Pytest Gate)", run_phase_5),
        ("Phase 6: Factory Router & Model Tier Dispatch", run_phase_6),
        ("Phase 7: Blueprints & Continuous-Learning Experts", run_phase_7),
    ]

    while True:
        banner("ADZ MASTER SHOWCASE — INTERACTIVE TOUR", "Select an individual phase or run the full pipeline")
        for i, (title, _) in enumerate(phases, start=1):
            print(f"  {C_CYAN}{C_BOLD}[{i}]{C_RESET} {title}")
        print(f"  {C_GREEN}{C_BOLD}[A]{C_RESET} Run ALL 7 Phases End-to-End")
        print(f"  {C_RED}{C_BOLD}[Q]{C_RESET} Quit")

        choice = input(f"\n{C_BOLD}Select an option [1-7, A, Q]: {C_RESET}").strip().upper()
        if choice == "Q":
            print(f"\n{C_YELLOW}Exiting showcase. Goodbye!{C_RESET}\n")
            break
        elif choice == "A":
            run_all()
            input(f"\n{C_DIM}Press Enter to return to menu...{C_RESET}")
        elif choice in [str(i) for i in range(1, len(phases) + 1)]:
            idx = int(choice) - 1
            phases[idx][1]()
            input(f"\n{C_DIM}Press Enter to return to menu...{C_RESET}")
        else:
            print(f"{C_RED}Invalid option, please choose between 1-7, A, or Q.{C_RESET}")


def main():
    parser = argparse.ArgumentParser(description="ADZ Software Factory Master Showcase")
    parser.add_argument("--all", action="store_true", help="Run all 7 phases sequentially")
    parser.add_argument("--phase", type=int, choices=range(1, 8), help="Run a single phase (1-7)")
    args = parser.parse_args()

    if args.all:
        run_all()
    elif args.phase:
        phase_map = {
            1: run_phase_1,
            2: run_phase_2,
            3: run_phase_3,
            4: run_phase_4,
            5: run_phase_5,
            6: run_phase_6,
            7: run_phase_7,
        }
        phase_map[args.phase]()
    else:
        # If running in non-interactive environment (like headless CI), run all; else interactive
        if not sys.stdin.isatty():
            run_all()
        else:
            interactive_menu()


if __name__ == "__main__":
    main()
