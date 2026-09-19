"""
meta_skill.py — Meta-Skills: a skill that generates and distributes
OTHER skills.

A skill is a directory:
  skills/<name>/SKILL.md   <- description + usage instructions
  skills/<name>/*          <- any supporting files

generate_skill() writes a new one from a natural-language need.
distribute_skill() copies a skill's directory into a warm E2B sandbox
(reusing sandbox_pool.py from the Sandbox Connector lesson) — the
"private distribution of your custom tools across different repos"
line from the lesson, made literal: same files, pushed to a new
location an agent working in that sandbox can read.
"""

import os
from pathlib import Path

from google import genai

SKILLS_DIR = Path(__file__).parent / "skills"
META_SKILL_MODEL = "gemini-3.8-flash"

META_SKILL_SYSTEM_INSTRUCTION = """
You write SKILL.md files describing a reusable capability for an AI
coding agent. Use exactly this structure:

# <Skill Name>

## When to use this
<1-2 sentences>

## Instructions
<numbered steps the agent should follow when this skill is invoked>

Output ONLY the markdown content — no explanation, no fences around it.
"""


def generate_skill(need: str, skill_name: str, skills_dir: Path = SKILLS_DIR) -> Path:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    interaction = client.interactions.create(
        model=META_SKILL_MODEL,
        system_instruction=META_SKILL_SYSTEM_INSTRUCTION,
        input=f"Write a SKILL.md for this need: {need}",
    )
    skill_dir = skills_dir / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(interaction.output_text.strip())
    return skill_dir


def distribute_skill(skill_dir: Path, sbx, target_path: str = "/workspace/repo/.claude/skills") -> None:
    """Push a skill directory into a warm sandbox (a WarmSandbox.sbx from
    SandboxPool.acquire()) so an agent working there gets the capability
    too, without re-deriving it from scratch."""
    sbx.commands.run(f"mkdir -p {target_path}/{skill_dir.name}")
    for file in skill_dir.iterdir():
        sbx.files.write(f"{target_path}/{skill_dir.name}/{file.name}", file.read_text())


if __name__ == "__main__":
    skill_path = generate_skill(
        need="Kick off both the frontend dev server and backend API server together, "
             "waiting for both to report ready before returning control.",
        skill_name="start_orchestrator",
    )
    print(f"Generated skill at: {skill_path}")
    print((skill_path / "SKILL.md").read_text())