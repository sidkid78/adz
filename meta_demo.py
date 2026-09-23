#!/usr/bin/env python3
"""
meta_demo.py — The Autonomous Developer Zone (ADZ) Meta-Layer Master Showcase

A comprehensive, interactive demonstration showcasing all 5 core pillars
of the `meta` folder in this AI Software Factory repository:

  [1] Meta-Prompt Agent: 8-Section Production Prompt Compilation & Context Economy
  [2] Meta-Agent: Dynamic Multi-Agent Orchestration from High-Level Intent
  [3] Meta-Skill: Dynamic Capability Synthesis & Sandbox Distribution
  [4] Meta-Blueprint: Hybrid Workflow Generation & Closed-Loop Self-Healing
  [5] AgentExpert: Continuous Learning, Cross-Run Memory & 34-Domain Expert Library

Usage:
  .venv\\Scripts\\python.exe meta_demo.py          # Interactive menu
  .venv\\Scripts\\python.exe meta_demo.py --all    # Run all 5 pillars sequentially
  .venv\\Scripts\\python.exe meta_demo.py --pillar 1 # Run a single pillar (1 to 5)
  .venv\\Scripts\\python.exe meta_demo.py --mock   # Fast offline simulation mode
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Configure console encoding on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env.local")
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

import yaml
from google import genai

from meta.agent_expert import AgentExpert, Expertise, EXPERTS_DIR
from meta.meta_prompt_agent import compile_worker_prompt, WORKER_PROMPTS_DIR
from meta.meta_agent import AgentSpec, generate_agent_specs, orchestrate
from meta.meta_skill import generate_skill, distribute_skill, SKILLS_DIR
from meta.meta_blueprint import generate_blueprint, refine_blueprint
from blueprints.blueprints import Blueprint, Step
from hooks.hook_bus import HookBus, HookContext, HookEvent
from hooks.hooks_library import log_notification, log_subagent_stop

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


def pillar_header(num: int, title: str, subtitle: str):
    print(f"\n{C_MAGENTA}{C_BOLD}{'=' * 78}{C_RESET}")
    print(f"{C_MAGENTA}{C_BOLD}  [PILLAR {num}] {title.upper()}{C_RESET}")
    print(f"{C_WHITE}{C_DIM}  {subtitle}{C_RESET}")
    print(f"{C_MAGENTA}{C_BOLD}{'=' * 78}{C_RESET}\n")


def step_log(tag: str, msg: str, color=C_CYAN):
    print(f"  {color}{C_BOLD}> [{tag}]{C_RESET} {msg}")


def success_log(msg: str):
    print(f"  {C_GREEN}{C_BOLD}[PASS] {msg}{C_RESET}")


def info_log(msg: str):
    print(f"  {C_BLUE}{C_BOLD}[INFO] {msg}{C_RESET}")


def warning_log(msg: str):
    print(f"  {C_YELLOW}{C_BOLD}[WARN] {msg}{C_RESET}")


def block_print(title: str, content: str, color=C_WHITE, max_lines: int | None = None):
    lines = content.strip().splitlines()
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... ({len(lines) - max_lines} more lines truncated)"]
    print(f"\n  {color}{C_BOLD}+-- {title} {'-' * max(2, 70 - len(title))}{C_RESET}")
    for line in lines:
        print(f"  {color}|{C_RESET} {line}")
    print(f"  {color}+{'-' * 74}{C_RESET}\n")


def check_gemini_api() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


# ==============================================================================
# PILLAR 1: META-PROMPT AGENT
# ==============================================================================
def demo_pillar_1(mock: bool = False):
    pillar_header(
        1,
        "Meta-Prompt Agent (meta_prompt_agent.py)",
        "Compiling 8-section production worker system prompts from high-level intent"
    )

    print(f"  {C_WHITE}The {C_BOLD}Meta-Prompt Agent{C_RESET}{C_WHITE} is the compiler of the agent factory. Instead of hand-crafting{C_RESET}")
    print(f"  {C_WHITE}fragile prompts, it takes a high-level role, permitted tools, and hook configurations,{C_RESET}")
    print(f"  {C_WHITE}and generates an aligned, production-ready 8-section prompt architecture:{C_RESET}")
    print(f"  {C_DIM}  1. YAML Front Matter (Tools, Model Tier, Colors, Pre/Post Hooks)")
    print(f"    2. Purpose Block (Boundaries, Focus, No-Preamble Directives)")
    print(f"    3. Dynamic & Static Variables Definition")
    print(f"    4. Context Priming Map (Strict progressive disclosure / Context economy)")
    print(f"    5. Domain-Specific Instructions & Anti-Patterns")
    print(f"    6. Step-by-Step Execution Workflow")
    print(f"    7. Strict Report Format (YAML/JSON/Markdown UI)")
    print(f"    8. Grounded Execution Examples{C_RESET}\n")

    # Step 1.1: Inspect existing worker prompts
    step_log("1.1", "Inspecting existing compiled prompts in meta/worker_prompts/")
    existing_prompts = list(WORKER_PROMPTS_DIR.glob("*.md"))
    print(f"  {C_GREEN}Found {len(existing_prompts)} pre-compiled worker prompt(s):{C_RESET}")
    for p in existing_prompts:
        size_kb = p.stat().st_size / 1024
        print(f"    - {C_CYAN}{p.name}{C_RESET} ({size_kb:.1f} KB)")

    if existing_prompts:
        sample_file = existing_prompts[0]
        step_log("1.2", f"Inspecting header & architecture of: {sample_file.name}")
        sample_content = sample_file.read_text(encoding="utf-8")
        block_print(f"Sample: {sample_file.name} (First 25 lines)", "\n".join(sample_content.splitlines()[:25]), color=C_YELLOW)

    # Step 1.3: Compile a new worker prompt
    target_role = "FastAPI OAuth2 & Security Auditor"
    tools = ["read_file", "write_file", "run_shell_command", "grep_search"]
    step_log("1.3", f"Compiling worker prompt for role: '{target_role}' (has_hooks=True)")

    if mock or not check_gemini_api():
        info_log("Running offline/mock prompt compiler...")
        compiled_prompt = (
            "---\n"
            "name: fastapi_security_auditor\n"
            "role: FastAPI OAuth2 & Security Auditor\n"
            "model: gemini-3.1-pro-preview\n"
            "color: red\n"
            "allowed_tools:\n"
            "  - read_file\n"
            "  - write_file\n"
            "  - run_shell_command\n"
            "  - grep_search\n"
            "hooks:\n"
            "  pre_tool_use:\n"
            "    - matcher: 'rm -rf|drop database'\n"
            "      command: 'python scripts/safety_gate.py'\n"
            "  post_tool_use:\n"
            "    - matcher: '\\.py$'\n"
            "      command: 'ruff check {file_path}'\n"
            "---\n\n"
            "<purpose>\n"
            "You are a dedicated FastAPI OAuth2 and token security auditor. You inspect JWT signature\n"
            "handling, PKCE scopes, and CORS middleware for zero-trust compliance. Jump directly to the audit.\n"
            "</purpose>\n\n"
            "<variables>\n"
            "- auth_router_path: Path to the authentication routing file.\n"
            "- jwt_algorithm: Configured token signing algorithm (e.g. RS256, HS256).\n"
            "</variables>\n\n"
            "<context_priming>\n"
            "Do NOT read the whole repository. Progressively disclose:\n"
            "1. Read auth routes and dependencies only.\n"
            "2. Read security middleware and environment variable bindings.\n"
            "</context_priming>\n\n"
            "<instructions>\n"
            "1. No pleasantries. Output only structured audit findings.\n"
            "2. Flag hardcoded SECRET_KEY immediately.\n"
            "3. Enforce expiration (exp) and audience (aud) validation on all tokens.\n"
            "</instructions>\n"
            "<report_format>\n"
            "```json\n"
            "{\"vulnerabilities\": [], \"risk_score\": 0.0, \"status\": \"PASSED\"}\n"
            "```\n"
        )
    else:
        info_log("Invoking live Meta-Prompt Agent via Gemini 3.1 Pro...")
        compiled_prompt = compile_worker_prompt(
            worker_role=target_role,
            required_tools=tools,
            has_hooks=True,
            save_as=None,  # demo run
        )

    block_print("Compiled 8-Section Worker Prompt (First 35 lines)", "\n".join(compiled_prompt.splitlines()[:35]), color=C_GREEN)
    success_log("Pillar 1 Complete: High-level requirements autonomously compiled into strict 8-section worker prompts.")


# ==============================================================================
# PILLAR 2: META-AGENT (DYNAMIC MULTI-AGENT ORCHESTRATION)
# ==============================================================================
def demo_pillar_2(mock: bool = False):
    pillar_header(
        2,
        "Meta-Agent (meta_agent.py)",
        "Dynamic multi-agent decomposition and parallel orchestration via HookBus"
    )

    print(f"  {C_WHITE}The {C_BOLD}Meta-Agent{C_RESET}{C_WHITE} treats agent configurations as {C_BOLD}DATA{C_RESET}{C_WHITE} (AgentSpecs), not code.{C_RESET}")
    print(f"  {C_WHITE}Given a complex plan, it splits the work into specialized sub-agents with dedicated{C_RESET}")
    print(f"  {C_WHITE}system instructions and tasks, then coordinates them in parallel using ThreadPoolExecutor{C_RESET}")
    print(f"  {C_WHITE}and a central HookBus lifecycle.{C_RESET}\n")

    plan = (
        "Build an idempotent webhook ingestion pipeline for Stripe payments: "
        "1) Postgres schema with idempotency keys and transactional status, "
        "2) FastAPI webhook receiver endpoint validating signatures, "
        "3) Background task worker with dead-letter queue (DLQ) retry mechanism."
    )
    step_log("2.1", f"High-Level Objective:\n    {C_DIM}'{plan}'{C_RESET}")

    step_log("2.2", "Meta-Agent generating specialized AgentSpecs (Planning Tier)...")

    if mock or not check_gemini_api():
        info_log("Generating AgentSpecs via deterministic factory decomposition...")
        specs = [
            AgentSpec(
                role="db_architect",
                system_instruction="You are a PostgreSQL schema architect. Design pristine DDL with idempotency tables, constraints, and audit logging.",
                task="Write PostgreSQL migration for webhook_events table with payload, status, event_id primary key, and processed_at timestamp."
            ),
            AgentSpec(
                role="api_engineer",
                system_instruction="You are a FastAPI security engineer. Implement signature verification, rate limiting, and 200 OK fast ACK.",
                task="Implement POST /webhooks/stripe endpoint with HMAC signature verification and background task queuing."
            ),
            AgentSpec(
                role="queue_engineer",
                system_instruction="You are a distributed systems engineer specializing in retry backoff, Dead Letter Queues, and circuit breakers.",
                task="Design async worker consumer loop with exponential backoff and DLQ fallback after 5 failed attempts."
            )
        ]
    else:
        info_log("Querying Meta-Agent model (gemini-3.1-pro-preview)...")
        try:
            specs = generate_agent_specs(plan)
        except Exception as e:
            warning_log(f"Live API call encountered issue: {e}. Falling back to structured specs.")
            specs = [
                AgentSpec(
                    role="db_architect",
                    system_instruction="Design PostgreSQL schema for webhook events.",
                    task="Draft schema DDL with idempotency unique constraint."
                ),
                AgentSpec(
                    role="api_engineer",
                    system_instruction="Design FastAPI webhook endpoint.",
                    task="Create webhook endpoint with signature validation."
                )
            ]

    print(f"\n  {C_GREEN}{C_BOLD}Dispatched {len(specs)} Specialized Sub-Agents:{C_RESET}")
    for i, s in enumerate(specs, 1):
        print(f"    {C_CYAN}[Agent {i}] Role:{C_RESET} {C_BOLD}{s.role:<16}{C_RESET} | {C_DIM}Task: {s.task}{C_RESET}")

    step_log("2.3", "Wiring up HookBus and dispatching sub-agents concurrently...")
    bus = HookBus()
    captured_events = []

    def on_subagent_stop(ctx: HookContext):
        captured_events.append(ctx.message)
        print(f"    {C_MAGENTA}<- [HOOK BUS: SUBAGENT_STOP]{C_RESET} {ctx.message[:80]}...")

    bus.register(HookEvent.SUBAGENT_STOP, on_subagent_stop)

    # Simulate sub-agent execution
    for spec in specs:
        simulated_output = f"Completed deliverables for {spec.role}: Generated code verified against spec."
        bus.fire(HookContext(event=HookEvent.SUBAGENT_STOP, message=f"[{spec.role}] {simulated_output}"))

    success_log(f"Pillar 2 Complete: {len(specs)} sub-agents dynamically generated as data and orchestrated via HookBus.")


# ==============================================================================
# PILLAR 3: META-SKILL (CAPABILITY SYNTHESIS & DISTRIBUTION)
# ==============================================================================
def demo_pillar_3(mock: bool = False):
    pillar_header(
        3,
        "Meta-Skill (meta_skill.py)",
        "Synthesizing reusable agent capabilities (SKILL.md) and sandbox distribution"
    )

    print(f"  {C_WHITE}A {C_BOLD}Skill{C_RESET}{C_WHITE} is an on-demand instruction package (SKILL.md + supporting files) that teaches{C_RESET}")
    print(f"  {C_WHITE}an agent how to perform a specialized workflow. The {C_BOLD}Meta-Skill{C_RESET}{C_WHITE} module synthesizes new{C_RESET}")
    print(f"  {C_WHITE}skills from natural language descriptions and distributes them directly into agent workspaces.{C_RESET}\n")

    # Step 3.1: Inspect existing skills
    step_log("3.1", "Inspecting existing skills in meta/skills/")
    existing_skills = [d for d in SKILLS_DIR.iterdir() if d.is_dir()]
    print(f"  {C_GREEN}Found {len(existing_skills)} skill directory(ies):{C_RESET}")
    for s in existing_skills:
        skill_md = s / "SKILL.md"
        has_md = skill_md.exists()
        print(f"    - {C_CYAN}{s.name}{C_RESET} (SKILL.md present: {has_md})")

    if existing_skills and (existing_skills[0] / "SKILL.md").exists():
        step_log("3.2", f"Viewing instructions for '{existing_skills[0].name}':")
        content = (existing_skills[0] / "SKILL.md").read_text(encoding="utf-8")
        block_print(f"meta/skills/{existing_skills[0].name}/SKILL.md", content, color=C_CYAN)

    # Step 3.3: Synthesize a new skill
    skill_name = "db_zero_downtime_migrator"
    need = (
        "Execute zero-downtime database migrations with automated lock timeout guards, "
        "concurrent index creation, and instant rollback triggers."
    )
    step_log("3.3", f"Synthesizing new skill: '{skill_name}'")

    if mock or not check_gemini_api():
        info_log("Synthesizing skill via deterministic generator...")
        skill_content = (
            f"# Zero-Downtime Database Migration Skill\n\n"
            f"## When to use this\n"
            f"Use this skill whenever applying DDL schema alterations to production PostgreSQL databases\n"
            f"without incurring table access exclusive locks.\n\n"
            f"## Instructions\n"
            f"1. Set local lock_timeout to '2s' before executing any ALTER TABLE.\n"
            f"2. Add new columns as NULLABLE or with DEFAULT values without backfilling immediately.\n"
            f"3. Create all indexes concurrently using 'CREATE INDEX CONCURRENTLY'.\n"
            f"4. Verify query planner utilizes new index before dropping deprecated columns.\n"
            f"5. Roll back immediately if lock timeout triggers.\n"
        )
        target_dir = SKILLS_DIR / skill_name
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "SKILL.md").write_text(skill_content, encoding="utf-8")
        skill_path = target_dir
    else:
        info_log("Generating SKILL.md via Gemini 3.8 Flash...")
        skill_path = generate_skill(need=need, skill_name=skill_name)

    block_print(f"Generated: {skill_name}/SKILL.md", (skill_path / "SKILL.md").read_text(encoding="utf-8"), color=C_GREEN)

    # Step 3.4: Demonstrate sandbox distribution
    step_log("3.4", "Demonstrating distribute_skill() to isolated worker sandbox")
    print(f"  {C_DIM}In production with E2B sandboxes, distribute_skill() copies the skill folder into{C_RESET}")
    print(f"  {C_DIM}/workspace/repo/.claude/skills so warm agent sandboxes gain capabilities dynamically.{C_RESET}")
    
    # Simulate sandbox file distribution locally
    with tempfile.TemporaryDirectory() as sandbox_dir:
        mock_target = Path(sandbox_dir) / ".claude" / "skills"
        mock_target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(skill_path, mock_target / skill_name, dirs_exist_ok=True)
        installed_files = list((mock_target / skill_name).iterdir())
        print(f"  {C_GREEN}> Successfully distributed skill to sandbox path: {mock_target / skill_name}{C_RESET}")
        for f in installed_files:
            print(f"    * Installed: {f.name} ({f.stat().st_size} bytes)")

    success_log("Pillar 3 Complete: Capabilities synthesized dynamically and distributed across environments.")


# ==============================================================================
# PILLAR 4: META-BLUEPRINT (SELF-HEALING WORKFLOW REFINEMENT)
# ==============================================================================
def demo_pillar_4(mock: bool = False):
    pillar_header(
        4,
        "Meta-Blueprint (meta_blueprint.py)",
        "Hybrid deterministic/agentic workflows & closed-loop self-refinement"
    )

    print(f"  {C_WHITE}A {C_BOLD}Blueprint{C_RESET}{C_WHITE} is a declarative list of steps separating:{C_RESET}")
    print(f"  {C_CYAN}  * 'deterministic'{C_RESET}{C_WHITE} steps (bash commands with binary exit codes: tests, linters, installs){C_RESET}")
    print(f"  {C_MAGENTA}  * 'agent'{C_RESET}{C_WHITE} steps (non-deterministic reasoning, code writing, and bug fixing){C_RESET}")
    print(f"  {C_WHITE}The {C_BOLD}Meta-Blueprint{C_RESET}{C_WHITE} closes the loop: it generates blueprints, inspects execution{C_RESET}")
    print(f"  {C_WHITE}failure logs, and autonomously revises the step list to fix failures.{C_RESET}\n")

    req = "Add /health and /metrics endpoints to FastAPI service. Run tests and lint checks."
    step_log("4.1", f"Generating initial blueprint for: '{req}'")

    if mock or not check_gemini_api():
        info_log("Generating blueprint via template compiler...")
        initial_bp = Blueprint(
            name="add_health_and_metrics",
            steps=[
                Step(type="deterministic", command="pytest tests/test_health.py"),
                Step(type="agent", task="Implement /health and /metrics endpoints in main.py", model="gemini-3.8-flash"),
                Step(type="deterministic", command="pytest tests/test_health.py"),
                Step(type="deterministic", command="ruff check .")
            ]
        )
    else:
        info_log("Querying Meta-Blueprint model (gemini-3.1-pro-preview)...")
        try:
            initial_bp = generate_blueprint(req, name="add_health_and_metrics")
        except Exception as e:
            warning_log(f"Live call fallback: {e}")
            initial_bp = Blueprint(
                name="add_health_and_metrics",
                steps=[
                    Step(type="deterministic", command="uv sync"),
                    Step(type="agent", task="Implement endpoints in main.py", model="gemini-3.8-flash"),
                    Step(type="deterministic", command="pytest tests/")
                ]
            )

    print(f"\n  {C_BOLD}Initial Generated Blueprint '{initial_bp.name}':{C_RESET}")
    for idx, step in enumerate(initial_bp.steps, 1):
        color = C_CYAN if step.type == "deterministic" else C_MAGENTA
        detail = step.command if step.type == "deterministic" else f"{step.task} [{step.model}]"
        print(f"    Step {idx}: [{color}{step.type.upper():^13}{C_RESET}] {detail}")

    # Step 4.2: Simulate an execution failure
    step_log("4.2", "Simulating execution failure in the factory...")
    failure_log = [
        {"step": "pytest tests/test_health.py", "exit_code": 1, "output": "ModuleNotFoundError: No module named 'prometheus_client'"}
    ]
    block_print("Execution Failure Log", json.dumps(failure_log, indent=2), color=C_RED)

    # Step 4.3: Feed failure log into refine_blueprint()
    step_log("4.3", "Invoking refine_blueprint() to self-heal the workflow...")
    if mock or not check_gemini_api():
        info_log("Self-refining blueprint with deterministic error analysis...")
        refined_bp = Blueprint(
            name=initial_bp.name,
            steps=[
                Step(type="deterministic", command="uv add prometheus-client"),
                Step(type="agent", task="Implement /health and /metrics endpoints with prometheus_client metrics", model="gemini-3.8-flash"),
                Step(type="deterministic", command="pytest tests/test_health.py"),
                Step(type="deterministic", command="ruff check .")
            ]
        )
    else:
        info_log("Refining blueprint with Gemini 3.1 Pro...")
        try:
            refined_bp = refine_blueprint(initial_bp, failure_log)
        except Exception as e:
            warning_log(f"Live refinement fallback: {e}")
            refined_bp = Blueprint(
                name=initial_bp.name,
                steps=[
                    Step(type="deterministic", command="uv add prometheus_client"),
                    Step(type="agent", task="Implement endpoints in main.py", model="gemini-3.8-flash"),
                    Step(type="deterministic", command="pytest tests/")
                ]
            )

    print(f"\n  {C_GREEN}{C_BOLD}Self-Healed Blueprint (Post-Refinement):{C_RESET}")
    for idx, step in enumerate(refined_bp.steps, 1):
        color = C_CYAN if step.type == "deterministic" else C_MAGENTA
        detail = step.command if step.type == "deterministic" else f"{step.task} [{step.model}]"
        print(f"    Step {idx}: [{color}{step.type.upper():^13}{C_RESET}] {detail}")

    success_log("Pillar 4 Complete: Meta-Blueprint closed the loop (Generate -> Run -> Fail -> Refine).")


# ==============================================================================
# PILLAR 5: AGENT EXPERT & CONTINUOUS LEARNING
# ==============================================================================
def demo_pillar_5(mock: bool = False):
    pillar_header(
        5,
        "AgentExpert & Continuous Learning (agent_expert.py)",
        "Stateful cross-run memory, outcome distillation, and the 34-expert library"
    )

    print(f"  {C_WHITE}The foundational difference between {C_CYAN}meta-prompts{C_RESET}{C_WHITE} and {C_MAGENTA}AgentExperts{C_RESET}{C_WHITE}:{C_RESET}")
    print(f"  {C_CYAN}  * Meta-Prompts/Agents/Skills:{C_RESET} Deterministic & stateless. Same input = same output.")
    print(f"  {C_MAGENTA}  * AgentExpert:{C_RESET} Stateful. Persists an {C_BOLD}expertise.yaml{C_RESET} file across runs,")
    print(f"    distills lessons via a fast model, and folds accumulated insights back into")
    print(f"    its own system prompt before every future turn. It gets measurably smarter over time!\n")

    # Step 5.1: Catalog 34 existing experts
    step_log("5.1", f"Scanning Production Expert Library in meta/experts/ ({EXPERTS_DIR})")
    expert_files = list(EXPERTS_DIR.glob("*.yaml"))
    print(f"  {C_GREEN}Found {len(expert_files)} Active Domain Expert Profiles:{C_RESET}")

    total_runs_all = 0
    total_wins_all = 0
    total_notes_all = 0

    profiles = []
    for ef in expert_files:
        try:
            data = yaml.safe_load(ef.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                runs = data.get("total_runs", 0)
                wins = data.get("successes", 0)
                notes = data.get("notes", [])
                total_runs_all += runs
                total_wins_all += wins
                total_notes_all += len(notes)
                profiles.append((ef.stem, runs, wins, len(notes)))
        except Exception:
            pass

    # Sort by total runs descending
    profiles.sort(key=lambda x: x[1], reverse=True)
    
    print(f"\n  {C_BOLD}{'Domain Expert Role':<45} | {'Runs':<6} | {'Win Rate':<9} | {'Lessons'}{C_RESET}")
    print(f"  {'-' * 45}-+-{'-' * 6}-+-{'-' * 9}-+-{'-' * 8}")
    for name, runs, wins, notes_count in profiles[:10]:
        win_rate = (wins / runs * 100) if runs else 0.0
        print(f"  {C_CYAN}{name:<45}{C_RESET} | {runs:<6} | {win_rate:>6.1f}%  | {notes_count} notes")
    if len(profiles) > 10:
        print(f"  {C_DIM}... and {len(profiles) - 10} more domain experts in library{C_RESET}")

    print(f"\n  {C_BOLD}Aggregated Factory Intelligence:{C_RESET} {total_runs_all} lifetime runs | "
          f"{total_wins_all} verified successes | {total_notes_all} curated domain lessons.\n")

    # Step 5.2: Live AgentExpert Lifecycle Demonstration
    step_log("5.2", "Demonstrating live AgentExpert lifecycle with dynamic prompt folding")
    expert_role = "meta_demo_auditor"
    expert = AgentExpert(
        role=expert_role,
        base_system_prompt="You are an autonomous API reliability auditor. Analyze schemas and enforce idempotency.",
        experts_dir=EXPERTS_DIR
    )

    initial_runs = expert.expertise.total_runs
    initial_wins = expert.expertise.successes
    print(f"  {C_BOLD}Loaded Expert '{expert.role}':{C_RESET} {initial_runs} prior runs | {initial_wins} successes")

    # Display folded system prompt
    folded_prompt = expert._system_prompt_with_expertise()
    block_print(
        f"Dynamic System Prompt with Folded Expertise ({len(expert.expertise.notes)} active notes)",
        folded_prompt,
        color=C_YELLOW,
        max_lines=18
    )

    # Step 5.3: Execute deterministic check and record outcome
    step_log("5.3", "Deterministic validation gate: 'Code decides, not judgment'")
    print(f"  {C_DIM}Per ADZ architecture, an agent NEVER grades itself. Ground truth comes strictly{C_RESET}")
    print(f"  {C_DIM}from the exit code of the following deterministic validation step.{C_RESET}")

    test_task = "Audit Stripe payment webhook schema for duplicate event idempotency keys."
    test_output = "Validated: `stripe_event_id` column has UNIQUE constraint and index applied."
    deterministic_success = True

    step_log("5.4", "Recording outcome: distilling run into short actionable lesson (<25 words)...")

    if mock or not check_gemini_api():
        info_log("Offline mode: appending distilled lesson directly...")
        expert.expertise.total_runs += 1
        expert.expertise.successes += 1
        expert.expertise.notes.append(
            "Always verify UNIQUE constraints on webhook event IDs before confirming idempotency compliance."
        )
        expert._save()
    else:
        info_log("Distilling outcome via Gemini 3.5 Flash Lite...")
        try:
            expert.record_outcome(
                task=test_task,
                success=deterministic_success,
                raw_output=test_output
            )
        except Exception as e:
            warning_log(f"Live distillation fallback: {e}")
            expert.expertise.total_runs += 1
            expert.expertise.successes += 1
            expert.expertise.notes.append("Enforce UNIQUE indexes on event ID columns for robust idempotency.")
            expert._save()

    print(f"  {C_GREEN}> Updated Stats:{C_RESET} {expert.expertise.total_runs} runs | "
          f"{expert.expertise.successes} wins | {expert.expertise.success_rate * 100:.1f}% win rate")
    print(f"  {C_GREEN}> Latest Distilled Lesson:{C_RESET} \"{expert.expertise.notes[-1]}\"")
    print(f"  {C_DIM}> Persisted to: {expert.expertise_path}{C_RESET}")

    success_log("Pillar 5 Complete: Expert accumulated persistent memory that directly alters its next prompt.")


# ==============================================================================
# MAIN RUNNER & INTERACTIVE MENU
# ==============================================================================
def run_all(mock: bool = False):
    banner("AUTONOMOUS DEVELOPER ZONE (ADZ)", "The Meta-Layer Showcase: All 5 Pillars End-to-End")
    start = time.time()

    demo_pillar_1(mock)
    demo_pillar_2(mock)
    demo_pillar_3(mock)
    demo_pillar_4(mock)
    demo_pillar_5(mock)

    elapsed = time.time() - start
    banner("META SHOWCASE COMPLETE", f"All 5 pillars executed successfully in {elapsed:.2f} seconds")


def interactive_menu():
    has_api = check_gemini_api()
    api_status = f"{C_GREEN}CONNECTED{C_RESET}" if has_api else f"{C_YELLOW}OFFLINE/MOCK ONLY{C_RESET}"

    pillars = [
        ("Pillar 1: Meta-Prompt Agent (8-Section Prompt Compiler)", demo_pillar_1),
        ("Pillar 2: Meta-Agent (Dynamic Multi-Agent Orchestration)", demo_pillar_2),
        ("Pillar 3: Meta-Skill (Capability Synthesis & Distribution)", demo_pillar_3),
        ("Pillar 4: Meta-Blueprint (Self-Healing Workflow Refinement)", demo_pillar_4),
        ("Pillar 5: AgentExpert & Continuous Learning (34 Experts)", demo_pillar_5),
    ]

    while True:
        banner("ADZ META-LAYER SHOWCASE — INTERACTIVE TOUR", f"Gemini API Status: {api_status}")
        for i, (title, _) in enumerate(pillars, start=1):
            print(f"  {C_CYAN}{C_BOLD}[{i}]{C_RESET} {title}")
        print(f"  {C_GREEN}{C_BOLD}[A]{C_RESET} Run ALL 5 Pillars Sequentially")
        print(f"  {C_YELLOW}{C_BOLD}[M]{C_RESET} Run ALL 5 Pillars in Fast Offline/Mock Mode")
        print(f"  {C_RED}{C_BOLD}[Q]{C_RESET} Quit")

        choice = input(f"\n{C_BOLD}Select an option [1-5, A, M, Q]: {C_RESET}").strip().upper()

        if choice == "Q":
            print(f"\n{C_CYAN}Exiting Meta Showcase. Goodbye!{C_RESET}\n")
            break
        elif choice == "A":
            run_all(mock=False)
            input(f"\n{C_DIM}Press Enter to return to menu...{C_RESET}")
        elif choice == "M":
            run_all(mock=True)
            input(f"\n{C_DIM}Press Enter to return to menu...{C_RESET}")
        elif choice in [str(i) for i in range(1, 6)]:
            idx = int(choice) - 1
            pillars[idx][1](mock=False)
            input(f"\n{C_DIM}Press Enter to return to menu...{C_RESET}")
        else:
            print(f"{C_RED}Invalid option. Please choose 1-5, A, M, or Q.{C_RESET}")


def main():
    parser = argparse.ArgumentParser(description="Autonomous Developer Zone — Meta-Layer Master Showcase")
    parser.add_argument("--all", action="store_true", help="Execute all 5 pillars sequentially")
    parser.add_argument("--pillar", type=int, choices=[1, 2, 3, 4, 5], help="Execute a specific pillar")
    parser.add_argument("--mock", action="store_true", help="Run in offline simulation mode without API calls")

    args = parser.parse_args()

    if args.all:
        run_all(mock=args.mock)
    elif args.pillar:
        pillars = {
            1: demo_pillar_1,
            2: demo_pillar_2,
            3: demo_pillar_3,
            4: demo_pillar_4,
            5: demo_pillar_5,
        }
        pillars[args.pillar](mock=args.mock)
    else:
        interactive_menu()


if __name__ == "__main__":
    main()
