"""
meta_agent.py — Meta-Agents: an orchestrator that builds agent CONFIGS
and spawns sub-agents from them, instead of every sub-agent being a
hand-written Python class.

An AgentSpec is generated DATA (a model tier, a system_instruction, a
task) — not code. The meta-agent call decides how to divide a plan
across sub-agents; the orchestrator then instantiates a real
HookedAgent (from the hooks lesson) per spec and runs them in parallel.
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from google import genai

from ..hooks.hook_bus import HookBus, HookContext, HookEvent
from ..hooks.hooks_library import log_notification, log_subagent_stop
from ..hooks.agentic_loop import HookedAgent

META_AGENT_MODEL = "gemini-3.1-pro-preview"   # planning-tier: deciding HOW to split work
SUBAGENT_MODEL = "gemini-3.6-flash"    # workhorse-tier: each sub-agent's actual work

META_AGENT_SYSTEM_INSTRUCTION = """
You are a meta-agent: you do not do the work yourself, you decide how
to split it across specialized sub-agents. Given a plan, respond with a
JSON array of sub-agent specs, each shaped exactly like:
  {"role": "<short role name>",
   "system_instruction": "<persona/instructions for this sub-agent>",
   "task": "<the specific task this sub-agent should do>"}
Return 2-4 specs. JSON only — no prose, no markdown fences.
"""


@dataclass
class AgentSpec:
    role: str
    system_instruction: str
    task: str


def generate_agent_specs(plan: str) -> list[AgentSpec]:
    """The meta-agent step: an LLM call whose output IS configuration
    for other agents, not an answer to the task itself."""
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    interaction = client.interactions.create(
        model=META_AGENT_MODEL,
        system_instruction=META_AGENT_SYSTEM_INSTRUCTION,
        input=f"Plan:\n{plan}",
    )
    raw = json.loads(interaction.output_text.strip())
    return [AgentSpec(**spec) for spec in raw]


def run_subagent(spec: AgentSpec, hook_bus: HookBus) -> str:
    agent = HookedAgent(model=SUBAGENT_MODEL, hook_bus=hook_bus, system_instruction=spec.system_instruction)
    result = agent.run(spec.task)
    hook_bus.fire(HookContext(event=HookEvent.SUBAGENT_STOP, message=f"[{spec.role}] {result}"))
    return result


def orchestrate(plan: str) -> dict[str, str]:
    specs = generate_agent_specs(plan)
    print(f"--- Meta-agent produced {len(specs)} sub-agent specs ---")
    for s in specs:
        print(f"  [{s.role}] {s.task}")

    bus = HookBus()
    bus.register(HookEvent.NOTIFICATION, log_notification)
    bus.register(HookEvent.SUBAGENT_STOP, log_subagent_stop)

    with ThreadPoolExecutor(max_workers=len(specs)) as pool:
        results = dict(zip(
            (s.role for s in specs),
            pool.map(lambda s: run_subagent(s, bus), specs),
        ))
    return results


if __name__ == "__main__":
    results = orchestrate(
        plan="Design a small blog platform: need a data model, a REST API, and a set of frontend components."
    )
    for role, output in results.items():
        print(f"\n=== {role} ===\n{output}")