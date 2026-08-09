"""
sandboxed_agent.py — HookedAgent variant that executes tools inside an
E2B sandbox + git worktree instead of the local machine.

Same PRE_TOOL_USE / POST_TOOL_USE wiring as agentic_loop.HookedAgent —
only WHERE write_file/run_shell_command actually execute changes.
"""

import os
from google import genai
from e2b import Sandbox

from test.hooks.hook_bus import HookBus, HookContext, HookEvent
from test.hooks.agentic_loop import TOOLS


def _execute_tool_in_sandbox(sbx: Sandbox, cwd: str, name: str, args: dict) -> dict:
    if name == "write_file":
        sbx.files.write(f"{cwd}/{args['path']}", args["content"])
        return {"status": "written", "path": args["path"]}
    if name == "run_shell_command":
        result = sbx.commands.run(args["command"], cwd=cwd)
        return {"exit_code": result.exit_code, "stdout": result.stdout, "stderr": result.stderr}
    return {"error": f"unknown tool {name}"}


class SandboxedHookedAgent:
    def __init__(self, model: str, hook_bus: HookBus, sbx: Sandbox, cwd: str,
                 system_instruction: str | None = None):
        self.client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.model = model
        self.hooks = hook_bus
        self.sbx = sbx
        self.cwd = cwd  # the worktree path this agent is confined to
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
                else:
                    result = _execute_tool_in_sandbox(self.sbx, self.cwd, call.name, call.arguments)
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