"""
blueprints.py — Blueprints: workflows as DATA, not hardcoded control flow

A Blueprint is a list of Steps, each either:
  "deterministic" -> a plain shell command, exit-code gated
  "agent"         -> a non-deterministic agent turn, optionally tied to
                      a persistent AgentExpert via expert_role

VALIDATION GATE (the fix from this lesson): when an agent step has
expert_role set, its success is NEVER taken from the agent's own output.
If the step immediately following it is deterministic, THAT step's
exit code becomes the ground truth passed to expert.record_outcome().
This is the same "code decides, not judgment" rule from the Hook Bus
lesson, applied to whether a persistent expert's memory gets updated.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

try:
    from hooks.hook_bus import HookBus
except ImportError:
    from hook_bus import HookBus

try:
    from sandbox.sandboxed_agent import SandboxedHookedAgent
except ImportError:
    try:
        from sandboxed_agent import SandboxedHookedAgent
    except ImportError:
        SandboxedHookedAgent = None

try:
    from meta.agent_expert import AgentExpert
except ImportError:
    from agent_expert import AgentExpert

try:
    from meta.meta_prompt_agent import compile_worker_prompt, WORKER_PROMPTS_DIR
except ImportError:
    from meta_prompt_agent import compile_worker_prompt, WORKER_PROMPTS_DIR

_EXPERT_CACHE: dict[str, AgentExpert] = {}


@dataclass
class Step:
    type: Literal["deterministic", "agent"]
    command: str | None = None       # deterministic steps
    task: str | None = None          # agent steps
    model: str | None = None         # agent steps
    expert_role: str | None = None   # agent steps — opts into persistent learning


@dataclass
class Blueprint:
    name: str
    steps: list[Step]


def get_or_create_expert(role: str) -> AgentExpert:
    """One AgentExpert instance per role per process — avoids reloading
    the same expertise.yaml repeatedly within one blueprint run. If no
    worker prompt exists yet for this role, the Meta-Prompt Agent
    compiles one on first use and it's cached to worker_prompts/ for
    every future run to reuse, expert or not."""
    if role in _EXPERT_CACHE:
        return _EXPERT_CACHE[role]

    prompt_path = WORKER_PROMPTS_DIR / f"{role}.md"
    if prompt_path.exists():
        base_prompt = prompt_path.read_text()
    else:
        base_prompt = compile_worker_prompt(
            worker_role=role.replace("_", " "),
            required_tools=["write_file", "run_shell_command"],
            has_hooks=True,
            save_as=role,
        )

    expert = AgentExpert(role=role, base_system_prompt=base_prompt)
    _EXPERT_CACHE[role] = expert
    return expert


def run_blueprint(blueprint: Blueprint, sbx, cwd: str, hook_bus: HookBus) -> list[dict]:
    log: list[dict] = []
    steps = blueprint.steps
    i = 0

    while i < len(steps):
        step = steps[i]

        if step.type == "deterministic":
            result = sbx.commands.run(step.command, cwd=cwd)
            log.append({"step": step.command, "exit_code": result.exit_code, "output": result.stdout + result.stderr})
            if result.exit_code != 0:
                log.append({"step": "ABORTED", "reason": f"deterministic step failed: {step.command}"})
                return log
            i += 1
            continue

        # step.type == "agent"
        if not step.expert_role:
            agent = SandboxedHookedAgent(model=step.model or "gemini-3.6-flash", hook_bus=hook_bus, sbx=sbx, cwd=cwd)
            output = agent.run(step.task)
            log.append({"step": step.task, "agent_output": output})
            i += 1
            continue

        # --- Agent step WITH a persistent expert attached ---
        expert = get_or_create_expert(step.expert_role)
        output = expert.run_sandboxed(step.task, sbx=sbx, cwd=cwd, hook_bus=hook_bus, model=step.model or "gemini-3.6-flash")
        log.append({"step": step.task, "agent_output": output, "expert_role": step.expert_role})

        has_validation_step = i + 1 < len(steps) and steps[i + 1].type == "deterministic"
        if not has_validation_step:
            # No deterministic step follows — there's no ground truth to
            # record against, so we deliberately do NOT call
            # record_outcome. An unverified "success" is worse than no
            # record at all.
            i += 1
            continue

        validation_step = steps[i + 1]
        result = sbx.commands.run(validation_step.command, cwd=cwd)
        log.append({"step": validation_step.command, "exit_code": result.exit_code, "output": result.stdout + result.stderr})

        success = result.exit_code == 0
        expert.record_outcome(task=step.task, success=success, raw_output=output)

        if not success:
            log.append({"step": "ABORTED", "reason": f"validation failed after expert '{step.expert_role}' step: {validation_step.command}"})
            return log

        i += 2  # consumed both the agent step and its validation step

    return log