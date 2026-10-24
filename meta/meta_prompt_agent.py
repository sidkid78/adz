"""
meta_prompt_agent.py — the production Meta-Prompt Agent

Upgrades meta_prompt.py's simple {{var}} command templates to the full
8-section worker prompt structure from the blueprint: YAML front matter
(with hooks), Purpose, Variables, Context Priming Map, Instructions,
Step-by-Step Workflow, Report Format, Grounded Examples.

META_PROMPT_AGENT_SYSTEM_INSTRUCTION below is reproduced from the
blueprint document as the compiler's own instruction, with one change:
the blueprint's rule 3 had the compiler pick Haiku/Sonnet/Opus. This
factory is Gemini and routes models itself, so compiled prompts were
telling a Gemini builder it was claude-3-5-sonnet. Rule 3 now forbids
naming a model at all.
"""

import os
from pathlib import Path

from google import genai

META_PROMPT_AGENT_MODEL = "gemini-3.1-pro-preview"  # compiling a production prompt is planning-tier work
WORKER_PROMPTS_DIR = Path(__file__).parent / "worker_prompts"

META_PROMPT_AGENT_SYSTEM_INSTRUCTION = """
# Purpose
You are a Meta-Prompt Agent specialized in prompt engineering, context engineering, and harness orchestration. Your job is to take a high-level task statement or worker role description and write a highly aligned, production-grade system prompt for a specialized worker agent. You compile vague human intent into structured, deterministic, and self-validating templates.

# Inputs
- worker_role: The primary focus or domain of the worker agent (e.g., "DB Migration Expert").
- required_tools: A list of CLI/MCP tools the agent is permitted to execute (e.g., "psql, ruff, pytest").
- has_hooks: Boolean ("true" or "false") indicating if the prompt needs deterministic self-validation hooks.

# Instructions
1. SPECIALIZATION IS THE CEILING: A focused agent with one purpose outperforms an unfocused agent with many purposes. Do not write generic prompts. Drill down to target the exact constraints of the domain.
2. HOOK BUS INTEGRATION: If has_hooks is true, write explicit post-tool use hooks in the YAML front matter to run deterministic type-checkers, compilers, or test suites, and write pre-tool hooks to intercept destructive commands (e.g., rm -rf).
3. NO MODEL SELECTION: Do not name a model or model family anywhere in the prompt. The harness chooses the model per ticket at run time; a model named here is never used, and it tells the agent reading this prompt that it is something it is not.
4. CONTEXT ECONOMY: Command the agent to conditionally prime its context window. It must never scan the whole codebase. It must progressively disclose files.
5. NO PLEASANTRIES: Force a strict "no-nonsense" professional tone. The output prompt must enforce "No pleasantries, no conversational preamble. Jump directly to the task."
6. XML SECTIONS: Structure the compiled system prompt using clear XML blocks to maximize model adherence.

# Output Format
Your output must be a single, self-contained Markdown block representing the compiled worker agent's system prompt, containing:
1. YAML Front Matter (Metadata, Tools, Color, and Hooks — no model).
2. Purpose Block.
3. Variable declarations.
4. Context priming mapping.
5. Domain-specific instructions (and anti-patterns to avoid).
6. Sequential step-by-step workflow (using ... to indicate progression rules).
7. Report Format (using strictly structured JSON, YAML, or HTML Generative UI).
8. Verified input/output examples.
"""


def compile_worker_prompt(worker_role: str, required_tools: list[str], has_hooks: bool,
                           save_as: str | None = None) -> str:
    """The Meta-Prompt Agent in action: worker_role/required_tools/has_hooks
    go in, a complete 8-section production system prompt comes out."""
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    interaction = client.interactions.create(
        model=META_PROMPT_AGENT_MODEL,
        system_instruction=META_PROMPT_AGENT_SYSTEM_INSTRUCTION,
        input=(
            f"worker_role: {worker_role}\n"
            f"required_tools: {', '.join(required_tools)}\n"
            f"has_hooks: {'true' if has_hooks else 'false'}"
        ),
    )
    compiled = interaction.output_text.strip()

    if save_as:
        WORKER_PROMPTS_DIR.mkdir(exist_ok=True)
        # Explicit utf-8: Windows defaults to cp1252, so an em dash was
        # cached as byte 0x97 and the utf-8 read in artifact_agent.py
        # crashed the NEXT run for that role — never the one that wrote it.
        (WORKER_PROMPTS_DIR / f"{save_as}.md").write_text(compiled, encoding="utf-8", newline="\n")

    return compiled


if __name__ == "__main__":
    prompt = compile_worker_prompt(
        worker_role="React component accessibility auditor",
        required_tools=["read_file", "run_shell_command"],
        has_hooks=True,
        save_as="a11y_auditor",
    )
    print(prompt)