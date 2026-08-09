"""
sandbox_workflow.py — E2B closed loop + GitHub handoff

Implements the standardized directory layout and the Request/Validate/
Resolve loop from the architecture doc, using the pieces already built
(GeminiBuildAgent, ROUTES from factory_router.py).

SCOPE NOTE: this still drives a single target_code.py + test file, same
as the earlier examples — not a full multi-file coding agent with its
own read/write/grep tools across an arbitrary repo. That's a genuinely
bigger project (giving the model function-calling tools + a real agent
loop). This shows the machinery AROUND that step — sandbox layout, git,
PR handoff — with the simplified build loop slotted in where a real
coding agent would eventually go.
"""

import os
import httpx
from e2b import Sandbox

from factory_router import ROUTES, classify_ticket
from gemini_build_agent import GeminiBuildAgent

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_API = "https://api.github.com"


def build_spec_file(ticket: dict) -> str:
    """Doc Step 1: write the GitHub issue body directly as the spec."""
    return f"# {ticket['title']}\n\n{ticket['description']}\n"


def setup_sandbox_layout(sbx: Sandbox, ticket: dict, base_branch: str) -> None:
    """Clones the repo and creates the standardized directory layout
    from the architecture doc: .claude/, ai_docs/, specs/, src/"""
    for d in [
        "/workspace/.claude/commands",
        "/workspace/.claude/hooks",
        "/workspace/ai_docs",
        "/workspace/specs",
    ]:
        sbx.commands.run(f"mkdir -p {d}")

    # Inject the token into the clone URL for auth. Never logged, never
    # written to a file — it only exists in the sandbox's process memory
    # for this one git command.
    clone_url = ticket["clone_url"].replace(
        "https://", f"https://x-access-token:{GITHUB_TOKEN}@"
    )
    sbx.commands.run(f"git clone -b {base_branch} {clone_url} /workspace/src")
    sbx.files.write("/workspace/specs/issue-spec.md", build_spec_file(ticket))


def run_closed_loop(sbx: Sandbox, build_model: str, max_retries: int) -> bool:
    """Doc Steps 2-6: agent reads the spec, writes code, a deterministic
    post-tool hook (pytest) validates, failures route back to the agent,
    repeat until pass or out of retries."""
    spec = sbx.files.read("/workspace/specs/issue-spec.md")
    agent = GeminiBuildAgent(model=build_model)
    code = agent.write_code(spec)

    for attempt in range(1, max_retries + 1):
        sbx.files.write("/workspace/src/target_code.py", code)

        # The "post-tool hook" from the doc: deterministic, same result
        # regardless of which model/agent produced the code.
        result = sbx.commands.run("pytest tests/ -v", cwd="/workspace/src")

        if result.exit_code == 0:
            print(f"Closed loop passed on attempt {attempt}")
            return True

        print(f"Attempt {attempt} failed, routing log back to agent")
        code = agent.fix_code(result.stdout + result.stderr)

    return False


def commit_and_push(sbx: Sandbox, branch: str, ticket: dict) -> None:
    sbx.commands.run(
        f"git -C /workspace/src checkout -b {branch} || git -C /workspace/src checkout {branch}"
    )
    sbx.commands.run("git -C /workspace/src add -A")
    sbx.commands.run(
        f'git -C /workspace/src commit -m "Resolve #{ticket["issue_number"]}: {ticket["title"]}"'
    )
    sbx.commands.run(f"git -C /workspace/src push origin {branch}")


def create_pull_request(ticket: dict, branch: str, base: str = "main") -> str:
    """GitHub Handoff Layer: opens a PR and links it back to the issue."""
    owner, repo = ticket["repo_full_name"].split("/")
    response = httpx.post(
        f"{GITHUB_API}/repos/{owner}/{repo}/pulls",
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        },
        json={
            "title": f"Fix: {ticket['title']} (closes #{ticket['issue_number']})",
            "head": branch,
            "base": base,
            "body": f"Automated fix for #{ticket['issue_number']}, generated and validated by the factory closed loop.",
        },
    )
    response.raise_for_status()
    return response.json()["html_url"]


def run_workflow(ticket: dict, base_branch: str = "main") -> None:
    """The full pipeline: classify -> provision -> closed loop -> handoff.
    This is what webhook_server.py hands off to as a background task."""
    ticket_type = classify_ticket(ticket)
    route = ROUTES[ticket_type]
    branch = f"factory/issue-{ticket['issue_number']}"

    print(f"=== Issue #{ticket['issue_number']} routed as {ticket_type.value.upper()} ===")

    with Sandbox.create(timeout=600) as sbx:
        setup_sandbox_layout(sbx, ticket, base_branch)
        passed = run_closed_loop(sbx, route["build_model"], route.get("max_retries", 3))

        if not passed:
            print(f"Issue #{ticket['issue_number']}: gave up, escalating to human")
            return

        commit_and_push(sbx, branch, ticket)

    pr_url = create_pull_request(ticket, branch, base=base_branch)
    print(f"Opened PR: {pr_url}")