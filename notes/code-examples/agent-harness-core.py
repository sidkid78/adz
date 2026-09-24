#!/usr/bin/env python3
"""
Customizable Agent Harness Core (Template Implementation)
Demonstrates programmatic wiring of Prompt Registry, Sandbox Connector, and Lifecycle Hook Bus.
"""

import os
import re
import sys
import json
import time
import logging

# Set up clean logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)


class PromptRegistry:
    """Manages system prompts, dynamic variables, and custom slash commands (prompt templates)."""
    
    def __init__(self):
        self.templates = {}
        self.global_system_prompt = (
            "You are a specialized engineering worker. Execute tasks surgically.\n"
            "Keep it simple stupid (KISS). Speed costs zero tokens."
        )

    def register_template(self, command_name: str, purpose: str, workflow: str, output_format: str):
        """Registers a reusable, structured prompt template."""
        self.templates[command_name] = {
            "purpose": purpose,
            "workflow": workflow,
            "output_format": output_format
        }
        logging.info(f"[Registry] Registered custom command: /{command_name}")

    def get_prompt(self, command_name: str, variables: dict) -> str:
        """Interpolates dynamic and static variables into a structured prompt."""
        if command_name not in self.templates:
            raise KeyError(f"Command /{command_name} not found in prompt registry.")
            
        template = self.templates[command_name]
        
        # Build prompt using the Stakeholder Trifecta format (Inputs, Workflow, Outputs)
        prompt_lines = [
            f"=== PURPOSE ===",
            template["purpose"],
            "\n=== INPUT VARIABLES ==="
        ]
        
        for k, v in variables.items():
            prompt_lines.append(f"{k}: {v}")
            
        prompt_lines.extend([
            "\n=== WORKFLOW ===",
            template["workflow"],
            "\n=== EXPECTED OUTPUT FORMAT ===",
            template["output_format"]
        ])
        
        compiled_prompt = "\n".join(prompt_lines)
        
        # Simple variable interpolation
        for k, v in variables.items():
            compiled_prompt = compiled_prompt.replace(f"{{{k}}}", str(v))
            
        return compiled_prompt

    def overwrite_system_prompt(self, new_prompt: str):
        """Overrides the global system prompt to steer model behavior."""
        self.global_system_prompt = new_prompt
        logging.info("[Registry] Overwrote global system prompt.")


class SandboxConnector:
    """Manages secure allocation, execution, and cleanup of pre-warmed dev environments."""
    
    def __init__(self, sandbox_provider="E2B"):
        self.provider = sandbox_provider
        self.active_sandboxes = {}
        logging.info(f"[Sandbox] Initialized Sandbox Connector using {self.provider}")

    def allocate_sandbox(self, session_id: str) -> str:
        """Simulates spawning a secure container sandbox in under 10 seconds."""
        sandbox_id = f"box_{session_id}_{int(time.time())}"
        self.active_sandboxes[session_id] = {
            "id": sandbox_id,
            "status": "active",
            "allocated_at": time.time(),
            "mounted_repo": "git@github.com:enterprise/codebase-singularity.git"
        }
        logging.info(f"[Sandbox] Allocated pre-warmed sandbox '{sandbox_id}' for session.")
        return sandbox_id

    def execute_command(self, session_id: str, command: str) -> dict:
        """Simulates executing terminal operations inside the isolated environment."""
        if session_id not in self.active_sandboxes:
            raise RuntimeError(f"No active sandbox session found for: {session_id}")
            
        logging.info(f"[Sandbox] [{self.active_sandboxes[session_id]['id']}] Executing: {command}")
        
        # Mock execution outputs based on commands
        if "rm -rf" in command:
            return {"return_code": 1, "stdout": "", "stderr": "Error: Command Blocked or Permission Denied."}
        elif "pytest" in command:
            return {"return_code": 0, "stdout": "==== 14 passed in 0.22s ====", "stderr": ""}
        elif "ruff" in command:
            return {"return_code": 0, "stdout": "All checks passed successfully.", "stderr": ""}
        else:
            return {"return_code": 0, "stdout": f"Executed successfully inside sandbox.", "stderr": ""}

    def destroy_sandbox(self, session_id: str):
        """Tears down the environment cleanly to avoid resource leaks."""
        if session_id in self.active_sandboxes:
            box_id = self.active_sandboxes[session_id]["id"]
            del self.active_sandboxes[session_id]
            logging.info(f"[Sandbox] Cleaned up and destroyed sandbox environment: {box_id}")


class LifecycleHookBus:
    """Manages registration, matching, and firing of deterministic lifecycle hooks."""
    
    def __init__(self):
        self.hooks = {
            "on_setup": [],
            "pre_tool_use": [],
            "post_tool_use": [],
            "on_stop": []
        }

    def register_hook(self, event_type: str, matcher_regex: str, callback_fn):
        """Registers a lifecycle hook callback with an associated pattern matcher."""
        if event_type not in self.hooks:
            raise KeyError(f"Invalid lifecycle hook event: {event_type}")
            
        self.hooks[event_type].append({
            "matcher": re.compile(matcher_regex),
            "callback": callback_fn
        })
        logging.info(f"[Hooks] Registered '{event_type}' hook matching: '{matcher_regex}'")

    def trigger_hook(self, event_type: str, context_payload: str) -> bool:
        """Fires hooks where the context payload matches the registered regex matcher."""
        if event_type not in self.hooks:
            return True
            
        hooks_to_run = self.hooks[event_type]
        if not hooks_to_run:
            return True
            
        success = True
        for hook in hooks_to_run:
            if hook["matcher"].search(context_payload):
                logging.info(f"[Hooks] [{event_type}] Match found! Triggering registered callback.")
                hook_success = hook["callback"](context_payload)
                if not hook_success:
                    success = False
                    
        return success


class AgentHarness:
    """Programmatically wires and orchestrates the custom Agentic Engineering Runtime."""
    
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.registry = PromptRegistry()
        self.sandbox = SandboxConnector()
        self.hooks = LifecycleHookBus()
        self.sandbox_id = None

    def initialize_runtime(self):
        """Boots the runtime, configures safety/validation hooks, and allocates the container."""
        # 1. Setup global hooks
        self.hooks.register_hook(
            event_type="pre_tool_use",
            matcher_regex=r"rm\s+-rf",
            callback_fn=self._security_firewall
        )
        
        self.hooks.register_hook(
            event_type="post_tool_use",
            matcher_regex=r"\.py$",
            callback_fn=self._auto_linter_validation
        )
        
        # 2. Trigger setup hook
        self.hooks.trigger_hook("on_setup", self.session_id)
        
        # 3. Provision isolated dev box
        self.sandbox_id = self.sandbox.allocate_sandbox(self.session_id)

    def execute_task(self, command_name: str, variables: dict):
        """Run loop simulating a secure, self-validating task execution."""
        logging.info(f"=== Starting Task Loop for Session: {self.session_id} ===")
        
        # Compile prompt
        compiled_prompt = self.registry.get_prompt(command_name, variables)
        logging.info(f"[Harness] Loaded Compiled Prompt:\n{compiled_prompt}\n")
        
        # Simulate agent tool steps
        target_file = variables.get("target_file", "app.py")
        
        # Simulating Tool Execution 1: Writing code
        write_command = f"echo 'print(\"hello\")' > {target_file}"
        self._run_tool_flow(write_command, target_file)
        
        # Simulating Tool Execution 2: Destructive Command (Security trigger)
        delete_command = f"rm -rf {target_file}"
        self._run_tool_flow(delete_command, target_file)

        # Simulated complete
        self.hooks.trigger_hook("on_stop", "success")
        self.sandbox.destroy_sandbox(self.session_id)

    def _run_tool_flow(self, command: str, target_file: str):
        """Internal helper running tools through pre/post hooks."""
        # Pre-Tool Firewall check
        if not self.hooks.trigger_hook("pre_tool_use", command):
            logging.error(f"[Harness] Command blocked by pre-tool firewall: {command}")
            return
            
        # Execute inside Sandbox
        result = self.sandbox.execute_command(self.session_id, command)
        
        # Post-Tool self-validation check
        self.hooks.trigger_hook("post_tool_use", target_file)

    def _security_firewall(self, cmd_payload: str) -> bool:
        """Deterministic safety check callback."""
        logging.warning(f"⚠️ [SAFETY] DESTRUCTIVE COMMAND BLOCK! Intercepted: {cmd_payload}")
        return False  # Blocks execution

    def _auto_linter_validation(self, file_path: str) -> bool:
        """Deterministic linter validation callback."""
        logging.info(f"🟢 [VALIDATION] Modified file detected: {file_path}. Running Ruff & Pytest checks...")
        # Auto-run checks in sandbox
        result = self.sandbox.execute_command(self.session_id, f"ruff check {file_path}")
        return True


# =====================================================================
# Verification Run
# =====================================================================
if __name__ == "__main__":
    harness = AgentHarness(session_id="session_6cdd6bd7")
    
    # Register a custom slash command template
    harness.registry.register_template(
        command_name="scout_plan_build",
        purpose="Locate codebase targets, devise a layout plan, and write features.",
        workflow="1. Search tree for {target_file}\n2. Create specification\n3. Execute UV build",
        output_format="JSON block containing final diff reports."
    )
    
    # Initialize Sandbox & Hook Pipeline
    harness.initialize_runtime()
    
    # Execute loop
    harness.execute_task(
        command_name="scout_plan_build",
        variables={"target_file": "billing_system.py", "issue_context": "Add Stripe invoice webhook."}
    )
