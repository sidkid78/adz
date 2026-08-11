"""
prompt_registry_demo.py — calling Gemini with a CompiledPrompt

This is the missing link: CompiledPrompt.system_instruction has to be
explicitly re-passed on EVERY interactions.create() call, even when
continuing a session with previous_interaction_id — the docs are
explicit that generation-time parameters like this are interaction-
scoped, not carried forward automatically. That's what call_model()
below enforces: you can never accidentally forget to pass it.
"""

import os
from google import genai

from prompt_registry import CompiledPrompt, PromptRegistry

DEFAULT_SYSTEM_INSTRUCTION = "You are a helpful, precise assistant."


def call_model(client: genai.Client, prompt: CompiledPrompt, model: str,
                previous_interaction_id: str | None = None):
    interaction = client.interactions.create(
        model=model,
        input=prompt.body,
        system_instruction=prompt.system_instruction or DEFAULT_SYSTEM_INSTRUCTION,
        previous_interaction_id=previous_interaction_id,
    )
    return interaction.output_text.strip(), interaction.id


def demo_variable_interpolation(registry: PromptRegistry, client: genai.Client):
    print("\n=== 1. Dynamic Variable Interpolation ===")
    prompt = registry.render(
        "build_feature",
        spec="A function `is_palindrome(s: str) -> bool` that ignores case and spaces.",
    )
    print(f"--- Compiled prompt sent to model ---\n{prompt.body}\n")
    output, _ = call_model(client, prompt, model="gemini-3.6-flash")
    print(f"--- Model output ---\n{output}")


def demo_system_prompt_override(registry: PromptRegistry, client: genai.Client):
    print("\n=== 3. System Prompt Overwriting (forced Markdown table) ===")
    prompt = registry.render(
        "review_code",
        code="def add(a,b):\n  return a+b\n\npassword = 'hunter2'  # hardcoded\n",
    )
    print(f"--- system_instruction override in effect ---\n{prompt.system_instruction}")
    output, _ = call_model(client, prompt, model="gemini-3.6-flash")
    print(f"--- Model output (should be a table, not prose) ---\n{output}")


def demo_higher_order_prompt(registry: PromptRegistry, client: genai.Client):
    print("\n=== 2. Higher-Order Prompt (Hop) ===")
    # Compile the INNER prompt first...
    inner = registry.render(
        "build_feature",
        spec="A function `slugify(title: str) -> str` for URL-safe slugs.",
    )
    # ...then pass the whole CompiledPrompt into the OUTER template as a variable.
    outer = registry.render("infinite_builder", sub_prompt=inner, iteration=1)
    print(f"--- Composed prompt (outer wraps inner) ---\n{outer.body}\n")
    output, _ = call_model(client, outer, model="gemini-3.6-flash")
    print(f"--- Model output (should end in a yaml status block) ---\n{output}")


if __name__ == "__main__":
    registry = PromptRegistry()
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    demo_variable_interpolation(registry, client)
    demo_system_prompt_override(registry, client)
    demo_higher_order_prompt(registry, client)