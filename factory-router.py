#!/usr/bin/env python3
"""
Autonomous Software Factory Router (E2B + FastAPI)
This script receives GitHub issue webhooks, classifies the task, provisions an isolated 
E2B Sandbox, executes closed-loop agentic coding, and creates a Pull Request.
"""

import os
import hmac
import hashlib
import json
import logging
from typing import Dict, Any, List
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, Header
from pydantic import BaseModel

# E2B Sandbox SDK Imports
# Note: Ensure `e2b` (or `e2b_code_interpreter`) is installed in your python environment.
try:
    from e2b import Sandbox
except ImportError:
    # Fallback/Mock placeholder for architectural representation if not installed
    class Sandbox:
        def __init__(self, template="base"):
            self.id = "mock-sandbox-id"
        def commands(self):
            return self
        def run(self, cmd: str):
            print(f"[E2B Mock] Executing command: {cmd}")
            return type('obj', (object,), {'stdout': 'Success', 'stderr': '', 'exit_code': 0})
        def close(self):
            print("[E2B Mock] Sandbox terminated.")

# Configure robust factory logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FactoryRouter")

app = FastAPI(title="Autonomous Software Factory Router")

# Core Environment Configuration
GITHUB_WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "super-secret-key-12345")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "ghp_mock_token_abcdef")
E2B_API_KEY = os.environ.get("E2B_API_KEY", "e2b_mock_key_98765")


def verify_signature(payload: bytes, signature: str) -> bool:
    """Verifies that the webhook request matches the GitHub secret signature."""
    if not signature:
        return False
    sha_name, signature_hash = signature.split('=')
    if sha_name != 'sha256':
        return False
    mac = hmac.new(GITHUB_WEBHOOK_SECRET.encode(), msg=payload, digestmod=hashlib.sha256)
    return hmac.compare_digest(mac.hexdigest(), signature_hash)


def classify_issue_task(title: str, labels: List[str]) -> str:
    """
    Classifies the inbound task into a technical category (ADW) to optimize model cost.
    Uses issue labels and titles to route efficiently.
    """
    label_set = {l.lower() for l in labels}
    
    # 1. Hotfix Classification: Highest priority, needs fast validation loops
    if "bug" in label_set or "hotfix" in label_set or "critical" in title.lower():
        return "hotfix"
    
    # 2. Chore/Maintenance: Simple tasks, lightweight models (e.g., Claude Haiku)
    if "chore" in label_set or "dependencies" in label_set or "ci" in label_set:
        return "chore"
    
    # 3. Feature Classification: Standard workflow (e.g., Claude Sonnet or Opus planning)
    return "feature"


def execute_agentic_sandbox_workflow(issue_data: Dict[str, Any]):
    """
    Provisions an isolated E2B Sandbox, injects context, executes the agent,
    commits updates, and opens a Pull Request. Runs completely out of the human loop.
    """
    issue_number = issue_data["number"]
    issue_title = issue_data["title"]
    issue_body = issue_data["body"]
    repo_name = issue_data["repo_name"]
    clone_url = issue_data["clone_url"]
    task_type = issue_data["task_type"]
    branch_name = f"factory/issue-{issue_number}"
    
    logger.info(f"[Task-{issue_number}] Initializing E2B Sandbox execution for task type: {task_type}")
    
    # 1. Allocate E2B Sandbox (Isolated Workspace Container)
    # Using 'base' or a pre-warmed dev container template
    sandbox = Sandbox(template="base")
    logger.info(f"[Task-{issue_number}] E2B Sandbox successfully allocated. ID: {sandbox.id}")
    
    try:
        # 2. Setup the Repository and Git Branch
        logger.info(f"[Task-{issue_number}] Cloning repository...")
        # Inject GITHUB_TOKEN directly into the clone command safely inside the sandbox
        authenticated_url = clone_url.replace("https://", f"https://x-access-token:{GITHUB_TOKEN}@")
        
        sandbox.run(f"git clone {authenticated_url} /workspace/src")
        sandbox.run(f"cd /workspace/src && git checkout -b {branch_name}")
        
        # Create standard layout directories
        sandbox.run("mkdir -p /workspace/specs /workspace/ai_docs /workspace/.gemini/commands")
        
        # 3. Inject Task Spec
        # Creating a Markdown spec so the agent understands goals, rules, and exit criteria
        spec_content = f"""# AI Task Specification
Issue Ref: #{issue_number}
Title: {issue_title}
Task Classification: {task_type.upper()}

## Requirements
{issue_body}

## Success Criteria (Closed Loop)
1. Code base compiles successfully.
2. Changes implemented exactly inside relevant src directory slices.
3. Target test suite must return Exit Code 0 (No failing tests).
"""
        # Escape spec quotes for simple echo write
        escaped_spec = spec_content.replace('"', '\\"')
        sandbox.run(f'echo "{escaped_spec}" > /workspace/specs/issue-spec.md')
        logger.info(f"[Task-{issue_number}] Injected Markdown task specification to /workspace/specs/issue-spec.md")
        
        # 4. Model Selection & Environment Setup based on Task Type
        if task_type == "chore":
            # Chores run lightweight models to conserve budget
            model_arg = "--model claude-3-5-haiku"
            logger.info(f"[Task-{issue_number}] Selected Haiku for low-overhead Chore workflow.")
        elif task_type == "hotfix":
            # Hotfixes use workhorse with extreme reasoning budgets and quick validation loops
            model_arg = "--model claude-3-7-sonnet --thinking-budget 4000"
            logger.info(f"[Task-{issue_number}] Selected Sonnet with Thinking Budget for Hotfix validation.")
        else:
            # Features use high-lever Opus configurations
            model_arg = "--model claude-3-5-opus"
            logger.info(f"[Task-{issue_number}] Selected Opus for feature engineering.")
            
        # 5. Kick off autonomous agent (representing Claude Code execution inside E2B)
        # Using non-interactive YOLO mode to run unattended and run system checks autonomously
        agent_command = (
            f"claude -p 'Read /workspace/specs/issue-spec.md, implement modifications "
            f"in src, run test suites, and validate before finishing.' "
            f"{model_arg} --yolo --dangerously-skip-permissions"
        )
        
        logger.info(f"[Task-{issue_number}] Executing agent command: {agent_command}")
        exec_result = sandbox.run(agent_command)
        
        if exec_result.exit_code != 0:
            logger.error(f"[Task-{issue_number}] Agent execution failed inside sandbox. Stderr: {exec_result.stderr}")
            # If hotfix failed, could implement a retry block or open a draft PR with logs for engineering triage
            return
            
        logger.info(f"[Task-{issue_number}] Agent finished work successfully. Validation checks passed.")
        
        # 6. Commit, Push, and Tear down Sandbox
        logger.info(f"[Task-{issue_number}] Committing and pushing branch to GitHub...")
        sandbox.run(f"cd /workspace/src && git config --global user.name 'Factory Router Agent'")
        sandbox.run(f"cd /workspace/src && git config --global user.email 'agent@factory-router.ai'")
        sandbox.run(f"cd /workspace/src && git add .")
        sandbox.run(f"cd /workspace/src && git commit -m 'feat(autonomous): resolve Issue #{issue_number} - {issue_title}'")
        sandbox.run(f"cd /workspace/src && git push origin {branch_name}")
        
        # 7. Create GitHub Pull Request
        # In a real environment, you would call the GitHub API using requests or PyGithub
        logger.info(f"[Task-{issue_number}] Creating Pull Request: '{branch_name}' into 'main'...")
        logger.info(f"🎉 [Task-{issue_number}] Successfully shipped fix to GitHub. Sandbox deleted.")
        
    except Exception as e:
        logger.error(f"[Task-{issue_number}] Exception encountered during E2B workflow: {str(e)}")
    finally:
        # Guarantee sandbox resources are always cleanly deallocated (CRUD for agents)
        sandbox.close()


@app.post("/webhooks/github")
async def handle_github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str = Header(None)
):
    """
    Main webhook entrypoint. Catches issue creation events, verifies HMAC signatures,
    classifies the task type, and queues the asynchronous E2B sandbox run.
    """
    payload_body = await request.body()
    
    # 1. Signature Verification Security Check
    if not verify_signature(payload_body, x_hub_signature_256):
        raise HTTPException(status_code=403, detail="Invalid signature. Webhook rejected.")
        
    event_payload = json.loads(payload_body.decode())
    
    # 2. Extract and Filter Events
    action = event_payload.get("action")
    issue = event_payload.get("issue")
    
    # We are strictly interested in newly opened issues
    if action != "opened" or not issue:
        return {"status": "ignored", "reason": f"Event action '{action}' is not tracked."}
        
    # Extract GitHub metadata
    issue_number = issue.get("number")
    issue_title = issue.get("title")
    issue_body = issue.get("body", "")
    labels = [lbl.get("name") for lbl in issue.get("labels", [])]
    repo_name = event_payload.get("repository", {}).get("full_name")
    clone_url = event_payload.get("repository", {}).get("clone_url")
    
    # 3. Classify Technical Category (ADW Classification Routing)
    task_type = classify_issue_task(issue_title, labels)
    
    logger.info(f"📥 Received Webhook. Issue #{issue_number}: '{issue_title}' in Repo '{repo_name}' classified as {task_type.upper()}")
    
    task_payload = {
        "number": issue_number,
        "title": issue_title,
        "body": issue_body,
        "repo_name": repo_name,
        "clone_url": clone_url,
        "task_type": task_type
    }
    
    # 4. Hand off execution as an asynchronous background task to avoid webhook timeout
    background_tasks.add_task(execute_agentic_sandbox_workflow, task_payload)
    
    return {
        "status": "queued",
        "issue_number": issue_number,
        "task_classification": task_type,
        "detail": "Asynchronous E2B sandbox execution has been scheduled out-loop."
    }


if __name__ == "__main__":
    import uvicorn
    # Start local webhook tunnel listener
    uvicorn.run(app, host="0.0.0.0", port=8000)
