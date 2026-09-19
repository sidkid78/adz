"""
spec_intake.py — turn anything in specs/ into tickets

The factory's front door. Point it at a directory (or one file) and it
produces tickets, choosing the cheapest ingestion route that works for
each input:

  .json   Already-decomposed orchestrator report? Parse it. Zero tokens,
          and it preserves the upstream system's own subtask boundaries,
          expertise tags and complexity ratings — information an LLM
          re-derivation would only approximate.
          A plain JSON list/object of tickets is also accepted as-is.
  .md .txt .rst
          Unstructured prose. Decompose with a model into atomic tickets.
  .yaml .yml
          Parsed first: if it is already ticket-shaped, use it directly;
          otherwise treat it as prose and decompose.

Deterministic first, model second — the same principle
ingest_orchestrator_report.py is built on. An LLM call is a fallback for
inputs that carry no structure, never the default.

Empty files are skipped rather than sent to a model to hallucinate over,
which is what `specs/sora-integration.md` (0 bytes) would otherwise get.
"""

import json
import os
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ingest_orchestrator_report import parse_tickets_from_json

PROSE_SUFFIXES = {".md", ".txt", ".rst"}
STRUCTURED_SUFFIXES = {".json", ".yaml", ".yml"}
SUPPORTED_SUFFIXES = PROSE_SUFFIXES | STRUCTURED_SUFFIXES


class IntakeResult:
    """One input file's outcome, so the caller can report what was
    ingested, what was skipped, and why — rather than silently
    processing a subset."""

    def __init__(self, path: Path, tickets: list[dict], method: str, note: str = ""):
        self.path = path
        self.tickets = tickets
        self.method = method
        self.note = note


def _looks_like_tickets(data) -> list[dict] | None:
    """Accept anything already shaped like tickets: a list of objects with
    a title and a description/body, or {"tickets": [...]}."""
    if isinstance(data, dict):
        for key in ("tickets", "items", "subtasks"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            return None
    if not isinstance(data, list) or not data:
        return None

    tickets = []
    for entry in data:
        if not isinstance(entry, dict):
            return None
        title = entry.get("title") or entry.get("name") or entry.get("summary")
        body = entry.get("description") or entry.get("body") or entry.get("content")
        if not title or not body:
            return None
        tickets.append({
            "title": str(title),
            "description": str(body),
            "source_subtask_id": entry.get("id") or entry.get("subtask_id"),
            "source_expertise": entry.get("expertise") or entry.get("role"),
            "source_complexity": entry.get("complexity"),
        })
    return tickets


def _decompose(path: Path, text: str, client=None) -> list[dict]:
    """Model-assisted decomposition, for inputs with no usable structure."""
    from decompose_into_tickets import DECOMPOSER_SYSTEM_INSTRUCTION

    if client is None:
        from google import genai
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    interaction = client.interactions.create(
        model="gemini-3.1-pro-preview",
        system_instruction=DECOMPOSER_SYSTEM_INSTRUCTION,
        input=text,
    )
    raw = interaction.output_text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]

    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError as exc:
        raise ValueError(f"decomposer returned non-JSON for {path.name}: {exc}") from exc

    tickets = _looks_like_tickets(data)
    if tickets is None:
        raise ValueError(f"decomposer output for {path.name} was not ticket-shaped")

    # Decomposed tickets carry no upstream complexity rating, so the
    # router will classify them itself. Tag their origin either way.
    for i, t in enumerate(tickets, 1):
        t.setdefault("source_subtask_id", None)
        if not t["source_subtask_id"]:
            t["source_subtask_id"] = f"{path.stem}_{i:02d}"
    return tickets


def ingest_file(path: Path, client=None, allow_model: bool = True) -> IntakeResult:
    """Ingest one spec file by the cheapest route that fits it."""
    if not path.exists():
        return IntakeResult(path, [], "skipped", "file not found")
    if path.stat().st_size == 0:
        return IntakeResult(path, [], "skipped", "empty file")

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        return IntakeResult(path, [], "skipped", f"unsupported type '{suffix}'")

    text = path.read_text(encoding="utf-8", errors="replace")

    if suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return IntakeResult(path, [], "failed", f"invalid JSON: {exc}")

        # Orchestrator report: worker steps carry metadata.subtask_id.
        if isinstance(data, dict) and data.get("execution_steps"):
            tickets = parse_tickets_from_json(path)
            return IntakeResult(path, tickets, "orchestrator report (0 tokens)")

        tickets = _looks_like_tickets(data)
        if tickets is not None:
            return IntakeResult(path, tickets, "ticket list (0 tokens)")
        return IntakeResult(path, [], "failed", "JSON is neither an orchestrator report nor ticket-shaped")

    if suffix in (".yaml", ".yml"):
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            return IntakeResult(path, [], "failed", f"invalid YAML: {exc}")
        tickets = _looks_like_tickets(data)
        if tickets is not None:
            return IntakeResult(path, tickets, "ticket list (0 tokens)")
        # Fall through: treat as prose.

    if not allow_model:
        return IntakeResult(path, [], "skipped", "needs model decomposition (disabled)")

    try:
        tickets = _decompose(path, text, client)
    except Exception as exc:  # noqa: BLE001 - one bad spec must not abort intake
        return IntakeResult(path, [], "failed", f"{type(exc).__name__}: {exc}")
    return IntakeResult(path, tickets, "model decomposition")


def ingest_path(target: Path, client=None, allow_model: bool = True) -> list[IntakeResult]:
    """Ingest a single file, or every supported file in a directory."""
    if target.is_file():
        return [ingest_file(target, client, allow_model)]
    files = sorted(p for p in target.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES)
    return [ingest_file(p, client, allow_model) for p in files]


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "specs"
    for result in ingest_path(target, allow_model=False):
        detail = result.note or f"{len(result.tickets)} ticket(s)"
        print(f"{result.path.name:30} {result.method:28} {detail}")
