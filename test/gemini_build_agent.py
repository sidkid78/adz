"""
gemini_build_agent.py  (Interactions API version)

Uses Gemini's new Interactions API instead of chats.create(). The key
difference: conversation state now lives on Google's server, addressed
by `previous_interaction_id` — a plain string. This is a closer literal
match to the "same session ID" language from Step 1 than the old chat
object was, and it sidesteps the client-lifetime bug entirely: even if
self.client got recreated, you could resume with just the ID string.

Current Gemini tiers (as of this writing):
  gemini-3.5-flash-lite  -> cheapest, fastest, high-volume/low-risk work
  gemini-3.6-flash       -> workhorse: good coding/agentic performance
  gemini-3.1-pro         -> flagship reasoning, best for planning
"""

import os
from google import genai

BUILD_AGENT_SYSTEM_PROMPT = """
You are a build agent. Your only job is to write or fix a single Python
file based on the instructions given to you.

Rules:
- Return ONLY raw Python code. No markdown fences, no explanation.
- Do not attempt to run, lint, or test the code yourself.
"""


class GeminiBuildAgent:
    def __init__(self, model: str):
        # Still keep a strong reference on self — good practice regardless
        # of which API you're calling. See googleapis/python-genai#1763.
        self.client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.model = model

        # Prime the session with the system prompt as the first turn.
        # The returned interaction.id IS the session ID — this is the
        # literal "same session ID" concept from your original text.
        interaction = self.client.interactions.create(
            model=model,
            input=BUILD_AGENT_SYSTEM_PROMPT,
        )
        self._last_interaction_id = interaction.id

    def _call(self, text: str) -> str:
        interaction = self.client.interactions.create(
            model=self.model,
            input=text,
            previous_interaction_id=self._last_interaction_id,
        )
        # Advance the pointer so the NEXT call continues from here.
        self._last_interaction_id = interaction.id
        return interaction.output_text.strip()

    def write_code(self, spec: str) -> str:
        return self._call(spec)

    def fix_code(self, error_log: str) -> str:
        return self._call(f"Your previous file failed a check:\n\n{error_log}\n\nFix it. Return only corrected code.")