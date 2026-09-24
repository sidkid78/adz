"""
build_agent.py  —  STEP 1: Isolated build agent

This file's only responsibility: turn a spec into code, or turn an error
log into fixed code. It has ZERO knowledge of pytest, ruff, mypy, or how
it's being checked. That's the whole point — you could swap the checker
underneath it and this file would never need to change.
"""

import os
from google import genai

BUILD_AGENT_PROMPT = """
You are a build agent. Your only job is to write or fix a single Python
file based on the instructions given to you.

Rules:
- Return ONLY raw Python code. No markdown fences, no explanation, no comments
  about what you changed.
- Do not attempt to run, lint, or test the code yourself — you have no way
  to do that. Just write it.
"""


class BuildAgent:
    """A thin wrapper around a single Gemini chat session."""

    def __init__(self, model: str = "gemini-3.8-flash"):
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self._chat = client.chats.create(model=model)
        # Prime the session once with its narrow job description.
        self._chat.send_message(BUILD_AGENT_PROMPT)

    def write_code(self, spec: str) -> str:
        """First draft, from a plain-language spec."""
        response = self._chat.send_message(spec)
        return response.text.strip()

    def fix_code(self, error_log: str) -> str:
        """Asked to fix its own previous output, given a raw error log.
        Note: this method doesn't know or care whether the log came from
        pytest, ruff, mypy, or something else entirely."""
        prompt = f"""
Your previous file failed a check. Here is the raw output:

{error_log}

Fix the file. Return only the corrected raw code.
"""
        response = self._chat.send_message(prompt)
        return response.text.strip()