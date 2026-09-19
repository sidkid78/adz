"""
ingest_orchestrator_report.py — deterministic ticket extraction from
orchestrator_workers execution reports (JSON export)

Reports from an orchestrator-workers run already contain individually
scoped worker outputs in execution_steps[], each tagged with a
subtask_id, a title, required expertise, and full content. That IS the
decomposition decompose_into_tickets.py has to reconstruct with an LLM
call when only given flattened markdown — when this JSON is available,
no LLM call is needed at all, just a parse.

Rule for identifying real worker steps vs orchestrator/synthesizer
bookends: a genuine worker step has metadata.subtask_id set. The Task
Orchestrator (planning) and Results Synthesizer (final assembly) steps
don't, and are deliberately excluded — they're not implementable
tickets, they're the framing AROUND the tickets.
"""

import json
from pathlib import Path


def parse_tickets_from_json(json_path: Path) -> list[dict]:
    data = json.loads(json_path.read_text(encoding="utf-8"))

    tickets = []
    for step in data.get("execution_steps", []):
        metadata = step.get("metadata", {})
        subtask_id = metadata.get("subtask_id")
        if not subtask_id:
            continue  # orchestrator/synthesizer bookend step, not a real ticket

        tickets.append({
            "title": metadata.get("title", subtask_id.replace("_", " ").title()),
            "description": step["content"],
            "source_subtask_id": subtask_id,
            "source_expertise": metadata.get("expertise"),
            "source_complexity": metadata.get("complexity"),
        })

    return tickets


if __name__ == "__main__":
    import sys
    result = parse_tickets_from_json(Path(sys.argv[1]))
    for t in result:
        print(f"[{t['source_complexity']}] {t['title']}  ({t['source_expertise']})  — {len(t['description'])} chars")