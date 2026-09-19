"""
agent_expert.py — Agent Experts: the piece meta-prompts, meta-agents,
and meta-skills explicitly are NOT.

Everything in meta_prompt.py / meta_agent.py / meta_skill.py is
deterministic generation: same inputs produce the same outputs, no
memory between runs. An AgentExpert is different — it persists an
"expertise.yaml" file that accumulates notes and success/failure
counts ACROSS runs, and folds that accumulated knowledge back into its
OWN system prompt on every subsequent run. It gets measurably different
over time; a meta-agent's AgentSpec never does — regenerate it twice
with the same input and you get the same (or randomly different, never
IMPROVED) output both times.
"""

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml  # uv add pyyaml
from google import genai

EXPERTS_DIR = Path(__file__).parent / "experts"
DISTILL_MODEL = "gemini-3.5-flash-lite"  # summarizing one run's outcome is cheap work
MAX_NOTES = 25  # bounded, so the expertise file can't grow forever


@dataclass
class Expertise:
    role: str
    total_runs: int = 0
    successes: int = 0
    failures: int = 0
    notes: list[str] = field(default_factory=list)
    last_updated: str | None = None

    @property
    def success_rate(self) -> float:
        return self.successes / self.total_runs if self.total_runs else 0.0


class AgentExpert:
    def __init__(self, role: str, base_system_prompt: str, experts_dir: Path = EXPERTS_DIR):
        self.role = role
        self.base_system_prompt = base_system_prompt
        self.expertise_path = experts_dir / f"{role}.yaml"
        self.client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.expertise = self._load()

    def _load(self) -> Expertise:
        if self.expertise_path.exists():
            data = yaml.safe_load(self.expertise_path.read_text())
            return Expertise(**data)
        return Expertise(role=self.role)

    def _save(self) -> None:
        self.expertise_path.parent.mkdir(exist_ok=True)
        self.expertise.last_updated = datetime.now(timezone.utc).isoformat()
        self.expertise_path.write_text(yaml.safe_dump(asdict(self.expertise), sort_keys=False))

    def _system_prompt_with_expertise(self) -> str:
        """The actual learning mechanism: accumulated notes get folded
        into the system prompt BEFORE the task runs, so past failures
        genuinely change future behavior — not just future logs."""
        if not self.expertise.notes:
            return self.base_system_prompt

        notes_block = "\n".join(f"- {n}" for n in self.expertise.notes[-MAX_NOTES:])
        return (
            f"{self.base_system_prompt}\n\n"
            f"# Accumulated Expertise ({self.expertise.total_runs} prior runs, "
            f"{self.expertise.success_rate:.0%} success rate)\n"
            f"Lessons from past runs — apply these:\n{notes_block}"
        )

    def run(self, task: str, model: str = "gemini-3.6-flash") -> str:
        interaction = self.client.interactions.create(
            model=model,
            system_instruction=self._system_prompt_with_expertise(),
            input=task,
        )
        return interaction.output_text.strip()

    def run_sandboxed(self, task: str, sbx, cwd: str, hook_bus, model: str = "gemini-3.6-flash") -> str:
        """Same tool-calling loop as SandboxedHookedAgent (write_file /
        run_shell_command, hooks fired at the same points) — this method
        adds nothing new to that loop except swapping in the expertise-
        augmented system prompt. No duplicated tool-execution logic."""
        from sandboxed_agent import SandboxedHookedAgent  # local import avoids a circular dependency

        agent = SandboxedHookedAgent(
            model=model, hook_bus=hook_bus, sbx=sbx, cwd=cwd,
            system_instruction=self._system_prompt_with_expertise(),
        )
        return agent.run(task)

    def record_outcome(self, task: str, success: bool, raw_output: str) -> None:
        """Distill the run into a short, reusable lesson instead of
        storing raw output — keeps the file bounded and the notes
        actually useful on a future read, not a growing transcript dump."""
        self.expertise.total_runs += 1
        if success:
            self.expertise.successes += 1
        else:
            self.expertise.failures += 1

        interaction = self.client.interactions.create(
            model=DISTILL_MODEL,
            system_instruction=(
                "Summarize this agent run into ONE short, actionable lesson "
                "(under 25 words) a future run of the SAME agent should know. "
                "Output only the lesson text, no prose around it."
            ),
            input=f"Task: {task}\nSucceeded: {success}\nOutput: {raw_output[:1000]}",
        )
        note = interaction.output_text.strip()
        self.expertise.notes.append(note)
        self.expertise.notes = self.expertise.notes[-MAX_NOTES:]

        self._save()


if __name__ == "__main__":
    from meta_prompt_agent import compile_worker_prompt

    base_prompt = compile_worker_prompt(
        worker_role="React component accessibility auditor",
        required_tools=["read_file"],
        has_hooks=False,
    )

    expert = AgentExpert(role="a11y_auditor", base_system_prompt=base_prompt)
    output = expert.run("Audit Button.tsx for missing aria-labels.")
    print(output)

    # NOTE: success=True is hardcoded here for demo purposes. In a real
    # pipeline this MUST come from an actual deterministic check (a
    # hook from hooks_library.py, a test run, a lint pass) — never from
    # the agent's own claim about whether it succeeded. See the Hook
    # Bus lesson: "code decides, not judgment."
    expert.record_outcome(task="Audit Button.tsx", success=True, raw_output=output)

    print(f"\n--- Expertise after this run ---\n{expert.expertise}")