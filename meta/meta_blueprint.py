"""
meta_blueprint.py — an agent whose job is to WRITE and REFINE Blueprint
step-lists. This closes the loop meta_prompt.py stopped short of:
generate -> run -> feed real failure back -> regenerate.
"""

import json
import os
from google import genai

from blueprints import Blueprint, Step

META_BLUEPRINT_MODEL = "gemini-3.1-pro"

META_BLUEPRINT_SYSTEM_INSTRUCTION = """
You design workflow blueprints for a coding agent factory. A blueprint
is a JSON array of steps, each either:
  {"type": "deterministic", "command": "<shell command>"}
  {"type": "agent", "task": "<what the sub-agent should do>", "model": "gemini-3.6-flash"}
Mix them: use deterministic steps for anything with one correct answer
(installing deps, running tests, linting), agent steps only for
genuinely non-deterministic work (writing or fixing code).
Return ONLY the JSON array, no prose, no markdown fences.
"""


def generate_blueprint(requirement: str, name: str) -> Blueprint:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    interaction = client.interactions.create(
        model=META_BLUEPRINT_MODEL,
        system_instruction=META_BLUEPRINT_SYSTEM_INSTRUCTION,
        input=f"Requirement: {requirement}",
    )
    raw_steps = json.loads(interaction.output_text.strip())
    return Blueprint(name=name, steps=[Step(**s) for s in raw_steps])


def refine_blueprint(blueprint: Blueprint, failure_log: list[dict]) -> Blueprint:
    """The self-improvement step: feed back exactly what run_blueprint()
    logged, ask for a revised step list. Same model, new evidence."""
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    current = json.dumps([s.__dict__ for s in blueprint.steps], indent=2)
    interaction = client.interactions.create(
        model=META_BLUEPRINT_MODEL,
        system_instruction=META_BLUEPRINT_SYSTEM_INSTRUCTION,
        input=(
            f"This blueprint failed:\n{current}\n\n"
            f"Execution log:\n{json.dumps(failure_log, indent=2)}\n\n"
            f"Revise the step list to fix the failure. Return the full corrected JSON array."
        ),
    )
    raw_steps = json.loads(interaction.output_text.strip())
    return Blueprint(name=blueprint.name, steps=[Step(**s) for s in raw_steps])


if __name__ == "__main__":
    bp = generate_blueprint(
        requirement="Add a new API endpoint /health that returns {'status': 'ok'}. Install deps first, then run tests.",
        name="add_health_endpoint",
    )
    for step in bp.steps:
        print(step)