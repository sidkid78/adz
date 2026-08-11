"""
agentic_loop.py — Gemini agent with tool use, wired through the Hook Bus

Every tool call the model requests passes through PRE_TOOL_USE before it
runs and POST_TOOL_USE after it runs. A nested subagent call fires
SUBAGENT_STOP when it finishes. STOP fires once when the whole run ends.
"""

import os
import subprocess
from google import genai

from .hook_bus import HookBus, HookContext, HookEvent

WRITE_FILE_TOOL = {
    "type": "function",
    "name": "write_file",
    "description": "Write content to a file on disk, overwriting any existing content.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to write"},
            "content": {"type": "string", "description": "Full file content"},
        },
        "required": ["path", "content"],
    },
}

RUN_SHELL_TOOL = {
    "type": "function",
    "name": "run_shell_command",
    "description": "Run a shell command and return its stdout, stderr, and exit code.",
    "parameters": {
        "type": "object",
        "properties": {"command": {"type": "string", "description": "The shell command to run"}},
        "required": ["command"],
    },
}

TOOLS = [WRITE_FILE_TOOL, RUN_SHELL_TOOL]


def _execute_tool(name: str, args: dict) -> dict:
    """Real, unhooked tool implementations. The hook bus wraps calls to
    these — this function itself has no idea hooks exist."""
    if name == "write_file":
        with open(args["path"], "w") as f:
            f.write(args["content"])
        return {"status": "written", "path": args["path"]}
    if name == "run_shell_command":
        result = subprocess.run(args["command"], shell=True, capture_output=True, text=True)
        return {"exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    return {"error": f"unknown tool {name}"}


class HookedAgent:
    def __init__(self, model: str, hook_bus: HookBus, system_instruction: str | None = None):
        self.client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.model = model
        self.hooks = hook_bus
        self.system_instruction = system_instruction
        self._last_interaction_id: str | None = None

    def run(self, task: str, max_steps: int = 6) -> str:
        interaction = self.client.interactions.create(
            model=self.model,
            input=task,
            system_instruction=self.system_instruction,
            tools=TOOLS,
            previous_interaction_id=self._last_interaction_id,
        )
        self._last_interaction_id = interaction.id

        for _ in range(max_steps):
            calls = [s for s in interaction.steps if s.type == "function_call"]

            if not calls:
                final_text = interaction.output_text.strip()
                self.hooks.fire(HookContext(event=HookEvent.STOP, message=final_text))
                return final_text

            results = []
            for call in calls:
                pre_decisions = self.hooks.fire(HookContext(
                    event=HookEvent.PRE_TOOL_USE, tool_name=call.name, tool_args=call.arguments
                ))
                blocked = next((d for d in pre_decisions if d.block), None)

                if blocked:
                    result = {"error": f"BLOCKED by hook: {blocked.reason}"}
                    self.hooks.fire(HookContext(
                        event=HookEvent.NOTIFICATION, message=f"Blocked {call.name}: {blocked.reason}"
                    ))
                else:
                    result = _execute_tool(call.name, call.arguments)
                    post_decisions = self.hooks.fire(HookContext(
                        event=HookEvent.POST_TOOL_USE, tool_name=call.name,
                        tool_args=call.arguments, tool_result=str(result),
                    ))
                    feedback = next((d.feedback for d in post_decisions if d.feedback), None)
                    if feedback:
                        result["hook_feedback"] = feedback

                results.append({"type": "function_result", "name": call.name, "call_id": call.id, "result": result})

            interaction = self.client.interactions.create(
                model=self.model,
                previous_interaction_id=self._last_interaction_id,
                input=results,
                tools=TOOLS,
            )
            self._last_interaction_id = interaction.id

        self.hooks.fire(HookContext(event=HookEvent.STOP, message="max_steps reached"))
        return "Stopped: max_steps reached without a final answer."


def run_subagent(task: str, model: str, hook_bus: HookBus) -> str:
    """A nested agent call. Fires SUBAGENT_STOP (not STOP) when it
    finishes, since it's a child run, not the top-level run."""
    subagent = HookedAgent(model=model, hook_bus=hook_bus)
    result = subagent.run(task)
    hook_bus.fire(HookContext(event=HookEvent.SUBAGENT_STOP, message=result))
    return result