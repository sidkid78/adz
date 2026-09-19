"""
factory_telemetry.py — structured events for a factory run

WHY THIS EXISTS
---------------
The first full build was opaque. Knowing whether it was progressing meant
grepping a log for "GATE PASSED", and knowing WHY a ticket needed three
attempts meant re-running it by hand. The run printed plenty of text and
recorded almost no facts.

So the factory now emits one JSON object per event to
factory_runs/<run_id>/events.jsonl, append-only, flushed immediately.
That file is the single source of truth for the dashboard, for
after-the-fact analysis, and for answering "what actually happened"
without re-running anything.

DESIGN NOTES
------------
- JSONL, not JSON: a run that crashes half way still leaves a valid,
  readable file up to the crash. That is exactly when you want the log.
- Flush every line. A buffered telemetry file is empty precisely when
  the process dies, which is when it matters most.
- Telemetry NEVER fails the build. Every emit is wrapped; a broken disk
  or a non-serialisable field degrades to a dropped event, not a lost
  build. The gate decides pass/fail, and it does not depend on this.
- Durations are recorded here rather than derived from timestamps in the
  dashboard, so a consumer never has to reconstruct pairs of events.
"""

import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
RUNS_DIR = REPO_ROOT / "factory_runs"

# Event kinds, named here so the dashboard and the emitters cannot drift.
RUN_START = "run_start"
RUN_END = "run_end"
SCAFFOLD = "scaffold"
LAYER_START = "layer_start"
TICKET_START = "ticket_start"
TICKET_END = "ticket_end"
ATTEMPT_END = "attempt_end"
GATE = "gate"
INTEGRATION_STEP = "integration_step"
REPAIR = "repair"
LOG = "log"


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class RunLog:
    """Append-only event log for one factory run."""

    run_id: str
    root: Path = RUNS_DIR
    started: float = field(default_factory=time.time)
    _seq: int = 0

    @property
    def run_dir(self) -> Path:
        return self.root / self.run_id

    @property
    def events_path(self) -> Path:
        return self.run_dir / "events.jsonl"

    def __post_init__(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        # A stable filename the dashboard can always resolve, so opening
        # the newest run never needs a directory listing.
        try:
            (self.root / "latest.txt").write_text(self.run_id, encoding="utf-8", newline="\n")
        except OSError:
            pass

    # ---- core ---------------------------------------------------------
    def emit(self, kind: str, **fields) -> None:
        self._seq += 1
        record = {
            "seq": self._seq,
            "ts": _now(),
            "elapsed": round(time.time() - self.started, 2),
            "kind": kind,
            **fields,
        }
        try:
            with self.events_path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        # Deliberately broad: a full disk or an unserialisable field must
        # cost one dropped event, never a build. The gate decides pass/fail
        # and does not depend on this file existing.
        except Exception:  # noqa: BLE001,S110
            pass

    def log(self, message: str, level: str = "info") -> None:
        self.emit(LOG, level=level, message=message)

    @contextmanager
    def timed(self, kind: str, **fields):
        """Emit `kind` once, on exit, with a measured duration.

        One event per completed thing rather than a start/end pair: the
        dashboard then never has to match them up, and a crash mid-step
        leaves an unmatched start it would have to special-case anyway.
        """
        start = time.time()
        outcome = {"ok": True}
        try:
            yield outcome
        except Exception as exc:
            outcome["ok"] = False
            outcome["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self.emit(kind, duration=round(time.time() - start, 2), **fields, **outcome)

    # ---- typed emitters ------------------------------------------------
    def run_start(self, architecture: str, repo: str, tickets: list[dict],
                  layers: list[list[str]]) -> None:
        self.emit(
            RUN_START, architecture=architecture, repo=repo,
            ticket_count=len(tickets), layers=layers,
            tickets=[
                {
                    "id": t["id"], "title": t.get("title"),
                    "complexity": t.get("complexity"),
                    "expertise": t.get("expertise"),
                    "spec_chars": len(t.get("architecture", "")),
                }
                for t in tickets
            ],
        )

    def ticket_start(self, ticket_id: str, layer: int, model: str, files: list[str],
                     max_attempts: int, expert: dict | None = None) -> None:
        self.emit(TICKET_START, ticket=ticket_id, layer=layer, model=model,
                  files=files, max_attempts=max_attempts, expert=expert)

    def attempt_end(self, ticket_id: str, attempt: int, outcome: str,
                    stage: str | None = None, detail: str | None = None,
                    duration: float | None = None, files_written: int | None = None) -> None:
        self.emit(ATTEMPT_END, ticket=ticket_id, attempt=attempt, outcome=outcome,
                  stage=stage, detail=detail, duration=duration, files_written=files_written)

    def ticket_end(self, ticket_id: str, ok: bool, attempts: int,
                   duration: float, detail: str = "") -> None:
        self.emit(TICKET_END, ticket=ticket_id, ok=ok, attempts=attempts,
                  duration=duration, detail=detail)

    def gate(self, command: str, passed: bool, duration: float,
             ticket: str | None = None, tail: str = "") -> None:
        # Keep a bounded tail: enough to see the error, not enough to turn
        # the event log into a transcript dump.
        self.emit(GATE, command=command, passed=passed, duration=duration,
                  ticket=ticket, tail=tail[-1200:])

    def integration_step(self, name: str, status: str, duration: float = 0.0,
                         reason: str = "", tail: str = "") -> None:
        self.emit(INTEGRATION_STEP, name=name, status=status, duration=duration,
                  reason=reason, tail=tail[-1200:])

    def repair(self, ticket_id: str, round_no: int, ok: bool, detail: str = "") -> None:
        self.emit(REPAIR, ticket=ticket_id, round=round_no, ok=ok, detail=detail)

    def run_end(self, built: list[str], failed: list[str], repaired: list[str],
                integration_passed: bool, integration_summary: str = "") -> None:
        self.emit(RUN_END, built=built, failed=failed, repaired=repaired,
                  integration_passed=integration_passed,
                  integration_summary=integration_summary,
                  duration=round(time.time() - self.started, 2))


class NullRunLog(RunLog):
    """Drops everything. Lets callers keep one code path when telemetry
    is switched off, instead of guarding every emit with `if log:`."""

    def __init__(self) -> None:
        super().__init__(run_id="null")

    def __post_init__(self) -> None:  # no directory, no latest.txt
        return

    def emit(self, kind: str, **fields) -> None:
        return


def new_run_id(name: str) -> str:
    # Local time on purpose: a run id is a label a person reads.
    # Every event carries a UTC `ts` for anything machine-ordered.
    return f"{datetime.now().astimezone().strftime('%Y%m%d_%H%M%S')}_{name}"


def read_events(run_id: str, root: Path = RUNS_DIR) -> list[dict]:
    """Read a run's events, tolerating a partial final line — a run that
    is still in flight is the normal case for a reader."""
    path = root / run_id / "events.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # torn last write; the next poll will pick it up
    return out


def latest_run_id(root: Path = RUNS_DIR) -> str | None:
    marker = root / "latest.txt"
    if marker.exists():
        rid = marker.read_text(encoding="utf-8").strip()
        if (root / rid).is_dir():
            return rid
    runs = sorted((p.name for p in root.glob("*") if p.is_dir()), reverse=True)
    return runs[0] if runs else None


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        # The dashboard fetches events.jsonl relative to itself, which the
        # file:// origin forbids. A plain static server is all it needs.
        import functools
        import http.server
        import socketserver

        port = int(sys.argv[2]) if len(sys.argv) > 2 else 8777
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                    directory=str(REPO_ROOT))
        print(f"Dashboard: http://localhost:{port}/dashboard.html")
        print(f"Serving   : {REPO_ROOT}   (Ctrl-C to stop)")
        with socketserver.TCPServer(("", port), handler) as httpd:
            httpd.serve_forever()
    else:
        rid = latest_run_id()
        if not rid:
            print("No runs yet.")
        else:
            events = read_events(rid)
            print(f"run {rid}: {len(events)} events")
            for e in events[-15:]:
                print(f"  {e['elapsed']:>8.2f}s  {e['kind']:<18} "
                      f"{e.get('ticket') or e.get('name') or e.get('message') or ''}")
