#!/usr/bin/env python3
"""
import_architecture.py — pull an orch2 technical architecture into specs/

orch2 writes technical architectures; this factory builds them. The two
live in different repos, so this is the seam between them.

What it preserves that the old nfo.json import threw away:

  dependencies    orch2's planner already knows planning_engine needs
                  kickoff_workflow's types to exist first. Building in
                  arbitrary order and hoping tsc forgives you is not a
                  factory, it's luck.
  output_format   "SQL Schema & ERD Description" / "Next.js Components &
                  API Routes" — the planner already said what shape the
                  deliverable takes. Re-deriving that with a pro-tier
                  call is slower AND less faithful to the plan.
  priority        the planner's own ordering within a dependency level.

It also CHECKS THE EXPORT IS COMPLETE. The nfo.json import silently
yielded 5 tickets from a plan of 6 because nothing compared the
planner's subtask_count against the worker steps actually present. A
short export now fails loudly instead of quietly building less than was
asked for.

Usage:
  python import_architecture.py <orch2-execution.json> [--name pm-mcp-server]
  python import_architecture.py --list <orch2-executions-dir>
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
SPECS_DIR = REPO_ROOT / "specs"


class IncompleteExport(Exception):
    """The report is missing worker output the planner said it planned."""


def load_architecture(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    steps = data.get("execution_steps") or []
    if not steps:
        raise ValueError(f"{path.name} has no execution_steps")

    planner = steps[0]
    plan = (planner.get("metadata") or {}).get("task_plan") or {}
    planned = {s["id"]: s for s in plan.get("subtasks", [])}

    # Worker steps carry metadata.subtask_id; the orchestrator and the
    # synthesizer bookends do not, and are deliberately excluded.
    delivered = {}
    for step in steps:
        meta = step.get("metadata") or {}
        sid = meta.get("subtask_id")
        if sid:
            delivered[sid] = (step, meta)

    missing = [sid for sid in planned if sid not in delivered]
    if missing:
        raise IncompleteExport(
            f"{path.name}: planner planned {len(planned)} subtask(s) but the export "
            f"contains {len(delivered)}. Missing worker output for: {', '.join(missing)}. "
            f"Re-export from orch2 — building a partial plan would ship an incomplete system."
        )

    tickets = []
    for sid, (step, meta) in delivered.items():
        spec = planned.get(sid, {})
        tickets.append({
            "id": sid,
            "title": meta.get("title") or spec.get("title") or sid,
            "expertise": meta.get("expertise") or spec.get("required_expertise"),
            "complexity": meta.get("complexity") or spec.get("estimated_complexity") or "medium",
            "output_format": meta.get("output_format") or spec.get("output_format") or "",
            "dependencies": list(spec.get("dependencies", [])),
            "priority": spec.get("priority", 99),
            # Paths the planner DECLARED, when it did. A path stated as
            # data cannot be missed the way one mentioned in prose can —
            # a bare migration filename was dropped by the path regex and
            # cost five downstream symptoms before the cause was found.
            # Absent on older exports, where scraping still applies.
            "files": list(meta.get("files") or spec.get("files") or []),
            # The functional position this subtask fills, when it competes
            # for one. Two tickets claiming the same slot are building the
            # same component; only one can be wired in.
            "target_slot": meta.get("target_slot") or spec.get("target_slot") or "",
            # The worker's full output: the technical content to build from.
            "architecture": step.get("content", ""),
            # The planner's framing of what this subtask is for.
            "intent": spec.get("description", ""),
        })

    tickets.sort(key=lambda t: (t["priority"], t["id"]))
    return {
        "execution_id": data.get("execution_id") or path.stem,
        "source": str(path),
        "user_query": (data.get("user_query") or "")[:2000],
        "execution_strategy": plan.get("execution_strategy", ""),
        "task_understanding": plan.get("task_understanding", ""),
        "success_metrics": plan.get("success_metrics", []),
        "tickets": tickets,
    }


def build_order(tickets: list[dict]) -> list[list[str]]:
    """Kahn layers: each inner list is a set of tickets whose dependencies
    are all satisfied, so they could be built in parallel."""
    remaining = {t["id"]: set(t["dependencies"]) for t in tickets}
    known = set(remaining)
    # Drop dependencies on things outside this plan rather than deadlock.
    for deps in remaining.values():
        deps &= known

    layers, done = [], set()
    while remaining:
        ready = sorted(tid for tid, deps in remaining.items() if deps <= done)
        if not ready:
            raise ValueError(f"dependency cycle among: {sorted(remaining)}")
        layers.append(ready)
        done |= set(ready)
        for tid in ready:
            del remaining[tid]
    return layers


def main() -> int:
    ap = argparse.ArgumentParser(description="Import an orch2 technical architecture into specs/")
    ap.add_argument("source", type=Path, help="orch2 execution .json (or a directory with --list)")
    ap.add_argument("--name", help="spec name (default: derived from the execution id)")
    ap.add_argument("--list", action="store_true", help="list candidate executions in a directory")
    args = ap.parse_args()

    if args.list:
        rows = []
        for f in sorted(args.source.glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8", errors="replace"))
            except Exception:  # noqa: BLE001,S112 - a listing skips files it cannot parse
                continue
            steps = d.get("execution_steps") or []
            n = sum(1 for s in steps if (s.get("metadata") or {}).get("subtask_id"))
            if n:
                rows.append((n, f.name, (d.get("user_query") or "")[:90].replace("\n", " ")))
        for n, name, q in sorted(rows, reverse=True)[:25]:
            print(f"{n:3} subtasks  {name}\n             {q}")
        return 0

    arch = load_architecture(args.source)
    name = args.name or arch["execution_id"]
    out = SPECS_DIR / f"{name}.architecture.json"
    SPECS_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(arch, indent=2), encoding="utf-8", newline="\n")

    print(f"Imported {len(arch['tickets'])} ticket(s) -> {out.relative_to(REPO_ROOT)}\n")
    for layer_no, layer in enumerate(build_order(arch["tickets"]), 1):
        print(f"  layer {layer_no} (buildable in parallel): {', '.join(layer)}")
    print()
    for t in arch["tickets"]:
        deps = ", ".join(t["dependencies"]) or "-"
        print(f"  [{t['complexity']:6}] {t['id']:22} {t['output_format'][:46]:46} deps: {deps}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except IncompleteExport as exc:
        print(f"INCOMPLETE EXPORT: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
