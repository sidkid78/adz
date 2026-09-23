"""
meta package — The Meta-Layer of the Autonomous Developer Zone (ADZ).

Components:
  - meta_prompt_agent: Compiles 8-section production worker agent prompts.
  - meta_agent: Decomposes plans into sub-agent specifications and orchestrates them.
  - meta_skill: Synthesizes reusable SKILL.md specs and handles sandbox distribution.
  - meta_blueprint: Generates and iteratively refines hybrid deterministic/agentic workflows.
  - agent_expert: Continuous-learning agents that accumulate knowledge across runs into YAML profiles.
"""

from .agent_expert import AgentExpert, Expertise
from .meta_prompt_agent import compile_worker_prompt
from .meta_agent import AgentSpec, generate_agent_specs, orchestrate
from .meta_skill import generate_skill, distribute_skill
from .meta_blueprint import generate_blueprint, refine_blueprint

__all__ = [
    "AgentExpert",
    "Expertise",
    "compile_worker_prompt",
    "AgentSpec",
    "generate_agent_specs",
    "orchestrate",
    "generate_skill",
    "distribute_skill",
    "generate_blueprint",
    "refine_blueprint",
]
