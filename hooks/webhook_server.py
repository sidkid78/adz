"""
webhook_server.py — GitHub webhook receiver -> Factory Router entrypoint

This is the "Factory Router (FastAPI)" box at the top of the architecture
diagram. It receives a GitHub Issue webhook, verifies the signature,
extracts the ticket, and dispatches to sandbox_workflow.run_workflow in
the background so GitHub gets an immediate 200 response instead of
timing out while the sandbox does its (potentially multi-minute) work.

Run: uv run uvicorn webhook_server:app --reload
"""

import hashlib
import hmac
import os

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from sandbox_workflow import run_workflow
from ci_repair_workflow import repair_ci_failure

app = FastAPI()

GITHUB_WEBHOOK_SECRET = os.environ["GITHUB_WEBHOOK_SECRET"]
FACTORY_ROUTER_SECRET = os.environ["FACTORY_ROUTER_SECRET"]


def verify_signature(payload: bytes, signature_header: str) -> None:
    """GitHub signs the raw payload with HMAC-SHA256 using your webhook
    secret. This check is deterministic and non-negotiable — same 'code
    decides, not judgment' philosophy as check.sh, just applied to trust
    instead of correctness: nothing downstream runs on unverified input."""
    if not signature_header:
        raise HTTPException(status_code=401, detail="Missing signature")

    expected = "sha256=" + hmac.new(
        GITHUB_WEBHOOK_SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, signature_header):
        raise HTTPException(status_code=401, detail="Invalid signature")


@app.post("/webhook/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str = Header(default=""),
    x_github_event: str = Header(default=""),
):
    raw_body = await request.body()
    verify_signature(raw_body, x_hub_signature_256)

    if x_github_event != "issues":
        return {"status": "ignored", "reason": f"unhandled event: {x_github_event}"}

    payload = await request.json()
    if payload.get("action") != "opened":
        return {"status": "ignored", "reason": "not a new issue"}

    issue = payload["issue"]
    ticket = {
        "title": issue["title"],
        "description": issue["body"] or "",
        "issue_number": issue["number"],
        "repo_full_name": payload["repository"]["full_name"],
        "clone_url": payload["repository"]["clone_url"],
    }

    # Respond to GitHub immediately; the real work happens after this returns.
    background_tasks.add_task(run_workflow, ticket)
    return {"status": "accepted", "issue": ticket["issue_number"]}


@app.post("/webhook/ci-failure")
async def ci_failure_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_factory_secret: str = Header(default=""),
):
    """Called by .github/workflows/factory-validation.yml's 'Notify
    Factory Router on CI Failure' step. This is a shared-secret check
    (matching the X-Factory-Secret header), not a GitHub HMAC signature
    — the guide's own security model, since this request originates
    from your CI runner, not from GitHub's webhook system."""
    if not x_factory_secret or not hmac.compare_digest(x_factory_secret, FACTORY_ROUTER_SECRET):
        raise HTTPException(status_code=401, detail="Invalid factory secret")

    payload = await request.json()
    background_tasks.add_task(repair_ci_failure, payload)
    return {"status": "accepted", "pr_number": payload.get("pr_number")}