"""
decompose_into_tickets.py — splits an oversized planning document (like
a full technical-architecture report) into N properly-scoped tickets,
each small enough to survive classify_ticket() -> run_closed_loop()
without breaking the single-file-closed-loop assumption those make.

This is the piece flagged missing when a 1,650-line architecture report
got traced through the pipeline: that kind of document is a PLAN, not a
ticket, and needs decomposition before it ever reaches webhook_server.py.
"""

import json
import os
from pathlib import Path

from google import genai

DECOMPOSER_MODEL = "gemini-3.1-pro-preview"  # splitting a large plan into scoped work is planning-tier

DECOMPOSER_SYSTEM_INSTRUCTION = """
You are a technical program manager. You are given a large planning or
architecture document. Split it into a list of INDIVIDUAL, atomically
scoped engineering tickets — each one small enough that a single agent
could implement it as ONE PR against ONE feature area, matching this
exact shape:

  {"title": "<short imperative title>",
   "description": "<a self-contained spec for JUST this piece — enough
    detail to implement without needing the rest of the document>"}

Rules:
- Never produce a ticket whose description spans more than one
  cohesive subsystem (e.g. do NOT combine 'database schema' and
  'payment integration' into one ticket).
- Strip business/marketing narrative — keep only what an engineer
  needs to implement the piece.
- Return 4-12 tickets. JSON array only, no prose, no markdown fences.
"""


def decompose_into_tickets(document_text: str, max_input_chars: int = 60000) -> list[dict]:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    interaction = client.interactions.create(
        model=DECOMPOSER_MODEL,
        system_instruction=DECOMPOSER_SYSTEM_INSTRUCTION,
        input=document_text[:max_input_chars],
    )
    return json.loads(interaction.output_text.strip())


def write_tickets_to_outbox(tickets: list[dict], outbox_dir: Path) -> list[Path]:
    """Written for human review, not auto-fired into run_workflow — same
    'engineer reviews at the end' principle from the very first lesson.
    Nothing in this function calls into webhook_server or sandbox_workflow."""
    outbox_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, ticket in enumerate(tickets, start=1):
        slug = ticket["title"].lower().replace(" ", "-")[:40]
        path = outbox_dir / f"{i:02d}-{slug}.md"
        path.write_text(f"# {ticket['title']}\n\n{ticket['description']}\n")
        paths.append(path)
    return paths