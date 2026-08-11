"""
blueprints.py — Blueprints: workflows as DATA, not hardcoded control flow

A Blueprint is a list of Steps, each either "deterministic" (a plain
shell command, exit-code gated) or "agent" (a non-deterministic
SandboxedHookedAgent turn). run_blueprint() is the ONE interpreter for
every blueprint — adding a new workflow never means writing new
orchestration code, just new Step data.
"""

from dataclasses import dataclass
from typing import Literal

from hook_bus import HookBus
from sandboxed_agent import SandboxedHookedAgent


@dataclass
class Step:
    type: Literal["deterministic", "agent"]
    command: str | None = None   # deterministic steps
    task: str | None = None      # agent steps
    model: str | None = None     # agent steps


@dataclass
class Blueprint:
    name: str
    steps: list[Step]


def run_blueprint(blueprint: Blueprint, sbx, cwd: str, hook_bus: HookBus) -> list[dict]:
    log = []
    for step in blueprint.steps:
        if step.type == "deterministic":
            result = sbx.commands.run(step.command, cwd=cwd)
            log.append({"step": step.command, "exit_code": result.exit_code, "output": result.stdout + result.stderr})
            if result.exit_code != 0:
                log.append({"step": "ABORTED", "reason": f"deterministic step failed: {step.command}"})
                return log
        elif step.type == "agent":
            agent = SandboxedHookedAgent(model=step.model or "gemini-3.6-flash", hook_bus=hook_bus, sbx=sbx, cwd=cwd)
            output = agent.run(step.task)
            log.append({"step": step.task, "agent_output": output})
    return log