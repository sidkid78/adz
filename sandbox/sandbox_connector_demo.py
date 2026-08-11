"""
sandbox_connector_demo.py — the full Sandbox Connector flow

1. Acquire ONE pre-warmed sandbox from the pool (fast — already cloned).
2. Create N ephemeral worktrees inside it, one per competing approach.
3. Race N SandboxedHookedAgents in parallel, each confined to its own
   worktree, each with its own sandbox-aware test hook.
4. First one whose tests pass gets committed, pushed, and turned into a PR.
5. Release the sandbox (destroyed — never reused for a different task).
"""

from dotenv import load_dotenv 

load_dotenv()

import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from e2b import Sandbox

from .sandbox_pool import SandboxPool
from .git_worktree import create_worktree, remove_worktree, Worktree
from test.hooks.hook_bus import HookBus, HookEvent
from test.hooks.hooks_library import block_destructive_commands, make_sandbox_test_hook, log_stop, log_notification
from .sandboxed_agent import SandboxedHookedAgent
from test.hooks.sandbox_workflow import create_pull_request  # reused from the earlier lesson


def run_one_attempt(sbx: Sandbox, repo_path: str, branch: str, task: str, model: str) -> Worktree | None:
    """One racer: its own worktree, its own hook bus (so its test hook
    is scoped to ITS worktree path, not someone else's)."""
    worktree = create_worktree(sbx, repo_path, branch)

    bus = HookBus()
    bus.register(HookEvent.PRE_TOOL_USE, block_destructive_commands)
    bus.register(HookEvent.POST_TOOL_USE, make_sandbox_test_hook(sbx, cwd=worktree.path))
    bus.register(HookEvent.NOTIFICATION, log_notification)
    bus.register(HookEvent.STOP, log_stop)

    agent = SandboxedHookedAgent(model=model, hook_bus=bus, sbx=sbx, cwd=worktree.path)
    agent.run(task)

    # Did it actually leave the worktree passing? Check independently of
    # whatever the agent claims — same "code decides, not the agent" rule
    # from every earlier lesson.
    result = sbx.commands.run("python -m pytest -x -q", cwd=worktree.path)
    if result.exit_code == 0:
        return worktree

    remove_worktree(sbx, repo_path, worktree)
    return None


def run_workflow(ticket: dict, model: str = "gemini-3.6-flash", n_racers: int = 3):
    pool = SandboxPool(pool_size=1)  # just need one warm sandbox for this demo
    warm = pool.acquire()

    try:
        with ThreadPoolExecutor(max_workers=n_racers) as executor:
            futures = {
                executor.submit(
                    run_one_attempt, warm.sbx, warm.repo_path,
                    f"factory/{ticket['issue_number']}-racer-{i}",
                    ticket["description"], model,
                ): i
                for i in range(n_racers)
            }

            winner: Worktree | None = None
            for future in as_completed(futures):
                worktree = future.result()
                if worktree and winner is None:
                    winner = worktree
                    print(f"Racer on branch '{worktree.branch}' won.")

        if not winner:
            print("All racers failed. Escalating to human.")
            return

        commit_msg = f"Resolve #{ticket['issue_number']}"
        warm.sbx.commands.run(f"git add -A && git commit -m '{commit_msg}'", cwd=winner.path)
        warm.sbx.commands.run(f"git push origin {winner.branch}", cwd=winner.path)

        pr_url = create_pull_request(ticket, branch=winner.branch)
        print(f"Opened PR: {pr_url}")

    finally:
        pool.release(warm)


if __name__ == "__main__":
    example_ticket = {
        "title": "Fix off-by-one in pagination",
        "description": "The paginate(items, page_size) function returns one extra item on the last page.",
        "issue_number": 42,
        "repo_full_name": "sidkid78/adz",
    }
    run_workflow(example_ticket)