"""
agent_harness.py — The Agent Harness

Composes every prior lesson into one run_session(), matching the
blueprint's "Harness Run Loop" pseudocode step-for-step.

Composition map (blueprint component -> file already built):
  Prompt Registry     -> prompt_registry.py
  Lifecycle Hook Bus   -> hook_bus.py + hooks_library.py
  Context Matcher      -> context_rules.py + scout_plan_build.py
  Sandbox Connector     -> sandbox_pool.py + git_worktree.py
  Tool loop w/ hooks    -> sandboxed_agent.py (SandboxedHookedAgent)
  GitHub Handoff        -> sandbox_workflow.create_pull_request
"""

import os
from pathlib import Path

from google import genai

from hooks.hook_bus import HookBus, HookContext, HookEvent
from hooks.hooks_library import (
    make_setup_hook,
    block_destructive_commands,
    make_sandbox_test_hook,
    log_notification,
    log_stop,
    log_subagent_stop,
)
from prompt_registry import PromptRegistry
from scout.context_rules import load_rules, rules_for_paths, compile_context_block
from scout.scout_plan_build import list_repo_files, scout, disclose, print_context_economy, PLANNER_MODEL
from sandbox.sandbox_pool import SandboxPool
from sandbox.git_worktree import create_worktree, remove_worktree
from sandbox.sandboxed_agent import SandboxedHookedAgent
from hooks.sandbox_workflow import create_pull_request

BUILD_MODEL = "gemini-3.8-flash"

# Session-specific setup — only runs for tickets that need it. Empty
# list is the common case; a database ticket might need ["alembic
# upgrade head"] here instead.
SESSION_SETUP_COMMANDS: list[str] = []


def run_session(ticket: dict, repo_root_for_scouting: str = ".") -> str | None:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    pool = SandboxPool(pool_size=1)
    warm = pool.acquire()
    worktree = create_worktree(warm.sbx, warm.repo_path, branch=f"factory/issue-{ticket['issue_number']}")

    # One hook bus for the whole session — every event type registered
    # up front, same bus fires SETUP, PRE/POST_TOOL_USE, and STOP.
    bus = HookBus()
    bus.register(HookEvent.SETUP, make_setup_hook(warm.sbx, worktree.path, SESSION_SETUP_COMMANDS))
    bus.register(HookEvent.PRE_TOOL_USE, block_destructive_commands)
    bus.register(HookEvent.POST_TOOL_USE, make_sandbox_test_hook(warm.sbx, cwd=worktree.path))
    bus.register(HookEvent.NOTIFICATION, log_notification)
    bus.register(HookEvent.STOP, log_stop)
    bus.register(HookEvent.SUBAGENT_STOP, log_subagent_stop)

    try:
        # --- Step 1: Initialize Workspace (Setup Hook) ---
        setup_decisions = bus.fire(HookContext(event=HookEvent.SETUP))
        setup_failure = next((d for d in setup_decisions if d.feedback), None)
        if setup_failure:
            print(f"Setup failed: {setup_failure.feedback}")
            return None

        # --- Step 2: Context Priming (Context Matcher) ---
        root = Path(repo_root_for_scouting)
        all_paths = list_repo_files(root)
        relevant_paths = scout(client, ticket["description"], all_paths)
        print_context_economy(root, all_paths, relevant_paths)

        matched_rules = rules_for_paths(load_rules(), relevant_paths)
        rules_block = compile_context_block(matched_rules)
        disclosed_code = disclose(root, relevant_paths)

        registry = PromptRegistry()
        spec_prompt = registry.render("build_feature", spec=ticket["description"])

        # --- Step 3: Planning Phase (no tools passed -> can only reason) ---
        planning_input = f"{rules_block}\n\n{spec_prompt.body}\n\n# Relevant files\n{disclosed_code}"
        plan_interaction = client.interactions.create(model=PLANNER_MODEL, input=planning_input)
        implementation_plan = plan_interaction.output_text.strip()
        print(f"--- Plan ---\n{implementation_plan}")

        # --- Step 4: Execution Phase (Tool Loop, hooks wired) ---
        agent = SandboxedHookedAgent(model=BUILD_MODEL, hook_bus=bus, sbx=warm.sbx, cwd=worktree.path)
        agent.run(implementation_plan)

        result = warm.sbx.commands.run("python -m pytest -x -q", cwd=worktree.path)
        if result.exit_code != 0:
            print("Session failed validation. Escalating to human.")
            return None

        commit_msg = f"Resolve #{ticket['issue_number']}: {ticket['title']}"
        warm.sbx.commands.run(f"git add -A && git commit -m '{commit_msg}'", cwd=worktree.path)
        warm.sbx.commands.run(f"git push origin {worktree.branch}", cwd=worktree.path)
        pr_url = create_pull_request(ticket, branch=worktree.branch)

        # --- Step 5: Tear Down and Summarize (Stop Hook) ---
        bus.fire(HookContext(event=HookEvent.STOP, message=f"Session complete. PR: {pr_url}"))
        return pr_url

    finally:
        remove_worktree(warm.sbx, warm.repo_path, worktree)
        pool.release(warm)


if __name__ == "__main__":
    example_ticket = {
        "title": "Fix off-by-one in pagination",
        "description": "paginate(items, page_size) returns one extra item on the last page.",
        "issue_number": 42,
        "repo_full_name": "your-org/your-repo",
    }
    run_session(example_ticket)