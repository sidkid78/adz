"""
ci_repair_workflow.py — the back half of the closed loop

factory-validation-guide.md's GitHub Actions workflow catches failures
AFTER a PR is already open (a second gate, independent of whatever
passed inside the sandbox before the PR was created — CI is the real
ground truth, not the sandbox's own pytest run). When it fails, it
POSTs to /webhook/ci-failure; this module does the actual repair:

  1. Retrieve Logs   -> fetch the failed job's real stdout/stderr via
                         the GitHub Actions API using run_id
  2. Spin Up Sandbox  -> reuse SandboxPool, but check OUT the agent's
                         EXISTING branch, not a fresh worktree off main
  3. Deploy Agent     -> same SandboxedHookedAgent + hook bus as every
                         prior lesson, given the real CI log as its task
  4. Re-Run Gate      -> push triggers the GitHub Actions workflow again
                         automatically — nothing else to do on our side
"""

import os
import httpx

from sandbox_pool import SandboxPool
from hook_bus import HookBus, HookEvent
from hooks_library import block_destructive_commands, make_sandbox_test_hook, log_notification, log_stop
from sandboxed_agent import SandboxedHookedAgent

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_API = "https://api.github.com"
MAX_LOG_CHARS = 8000  # keep the tail — the actual failure is almost always near the end


def get_failed_job_logs(owner: str, repo: str, run_id: int) -> str:
    """Step 1: Retrieve Logs. GitHub's job-logs endpoint redirects to a
    plain-text archive; httpx follows it automatically."""
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}

    jobs_resp = httpx.get(f"{GITHUB_API}/repos/{owner}/{repo}/actions/runs/{run_id}/jobs", headers=headers)
    jobs_resp.raise_for_status()
    failed_jobs = [j for j in jobs_resp.json()["jobs"] if j["conclusion"] == "failure"]

    logs = []
    for job in failed_jobs:
        log_resp = httpx.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/actions/jobs/{job['id']}/logs",
            headers=headers, follow_redirects=True,
        )
        log_resp.raise_for_status()
        logs.append(f"=== Job: {job['name']} ===\n{log_resp.text}")

    return "\n\n".join(logs)[-MAX_LOG_CHARS:]


def repair_ci_failure(payload: dict) -> None:
    """The full repair cycle. Called from webhook_server.py's
    /webhook/ci-failure route as a background task."""
    owner, repo = payload["repository"].split("/")
    run_id = payload["run_id"]
    branch = payload["branch_name"]
    pr_number = payload["pr_number"]

    print(f"=== CI failed on PR #{pr_number} (branch {branch}), fetching logs ===")
    logs = get_failed_job_logs(owner, repo, run_id)

    # Step 2: Spin Up Sandbox
    pool = SandboxPool(pool_size=1)
    warm = pool.acquire()

    try:
        # Check out the EXISTING agent branch — this is a repair, not a
        # fresh attempt, so it must build on the branch CI actually ran.
        warm.sbx.commands.run(f"git fetch origin {branch}", cwd=warm.repo_path)
        warm.sbx.commands.run(f"git checkout {branch}", cwd=warm.repo_path)

        bus = HookBus()
        bus.register(HookEvent.PRE_TOOL_USE, block_destructive_commands)
        bus.register(HookEvent.POST_TOOL_USE, make_sandbox_test_hook(warm.sbx, cwd=warm.repo_path))
        bus.register(HookEvent.NOTIFICATION, log_notification)
        bus.register(HookEvent.STOP, log_stop)

        # Step 3: Deploy Agent — same instruction language as the guide's
        # own spec, so the agent's task matches what a human reviewer
        # would expect from reading the guide.
        agent = SandboxedHookedAgent(model="gemini-3.6-flash", hook_bus=bus, sbx=warm.sbx, cwd=warm.repo_path)
        agent.run(
            "CI validation failed on this branch. Fix the linter/test errors "
            "detailed in these logs, then stop once local pytest passes.\n\n"
            f"CI failure logs:\n{logs}"
        )

        # Ground truth, not the agent's claim — same rule as every prior
        # lesson: an independent check decides success, not self-report.
        result = warm.sbx.commands.run("python -m pytest -x -q", cwd=warm.repo_path)
        if result.exit_code != 0:
            print(f"Repair attempt failed to fix PR #{pr_number}. Escalating to human.")
            return

        warm.sbx.commands.run(
            "git add -A && git commit -m 'Auto-repair: fix CI failure'", cwd=warm.repo_path
        )
        warm.sbx.commands.run(f"git push origin {branch}", cwd=warm.repo_path)

        # Step 4: Re-Run Gate — the push above is the entire mechanism.
        # GitHub Actions re-triggers factory-validation.yml on its own;
        # there's nothing further for this function to do.
        print(f"Pushed repair commit to {branch}. CI will re-run automatically.")

    finally:
        pool.release(warm)