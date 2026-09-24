#!/usr/bin/env python3
"""
architecture_watcher.py — orch2 finishes an architecture, the factory builds it

The last manual seam. orch2 writes a technical architecture to its
executions directory; until now somebody had to notice, run
import_architecture.py, then run build_architecture.py. This closes that
loop: a finished architecture is picked up, imported, and built.

WHAT MAKES THIS SAFE TO LEAVE RUNNING
-------------------------------------
A watcher that starts an expensive, token-spending build on its own needs
more care than one that copies a file, so:

  SEEDED, NOT BACKFILLED  On startup every existing execution is recorded
      as already-seen WITHOUT building it. orch2 has 135 of them; a
      watcher that "catches up" on boot would launch 135 builds.
  READINESS, NOT ARRIVAL  A file event means "something was written", not
      "the architecture is finished". A file is only accepted once its
      size has stopped changing AND import_architecture parses it with
      every planned subtask present — the same completeness guard that
      caught the truncated nfo.json export.
  ROUTING  orch2 plans consulting work as well as software. When no
      ticket names a file (changesets.deliverable_kind), the deliverable
      is prose, and it goes to run_factory.py — the document factory —
      instead of being built as a Next.js app from guessed paths. An
      architecture that is code but yields no file contracts at all is
      reported and skipped.
  A LEDGER  Every decision is appended to factory_runs/watcher.jsonl, so
      "why did it skip that one" is answerable after the fact.
  A CEILING  --max-tickets refuses plans larger than expected rather than
      discovering the cost afterwards.
  ONE AT A TIME  Builds are serialised. Two concurrent runs would fight
      over the Supabase port block and npm cache.

Usage:
  python architecture_watcher.py                      # watch, build what lands
  python architecture_watcher.py --plan-only          # import + plan, never build
  python architecture_watcher.py --once <file.json>   # process one file and exit
  python architecture_watcher.py --watch-dir <dir>    # non-default orch2 location
"""

import argparse
import json
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from changesets import ChangesetError, contract_for_ticket, deliverable_kind
from import_architecture import IncompleteExport, build_order, load_architecture

DEFAULT_WATCH_DIR = Path(
    r"C:/Users/sidki/source/repos/orch2/backend/workflow_logs/executions"
)
SPECS_DIR = REPO_ROOT / "specs"
LEDGER = REPO_ROOT / "factory_runs" / "watcher.jsonl"

# How long a file's size must hold steady before it is considered written.
STABLE_SECONDS = 4.0
STABLE_POLL = 1.0
# How long to keep waiting for a file that is still growing or still
# missing worker output, before giving up on this event.
READY_TIMEOUT = 300.0


def record(event: str, **fields) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    line = {"ts": datetime.now(UTC).isoformat(), "event": event, **fields}
    try:
        with LEDGER.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(line, default=str) + "\n")
    except OSError:
        pass
    detail = " ".join(f"{k}={v}" for k, v in fields.items() if k != "detail")
    print(f"[watcher] {event}: {detail}")
    if fields.get("detail"):
        print(f"           {fields['detail']}")


def slug_for(arch: dict, path: Path) -> str:
    """A readable repo name. Prefers words from the request over the
    timestamp id, because `factory_workspace/20260311_115625_...` tells a
    human nothing about what is in it."""
    import re

    query = (arch.get("user_query") or "").lower()
    # Skip the boilerplate these requests open with.
    query = re.sub(
        r"(implement|logic|technical|architecture|for the following|using|need|help me|"
        r"comprehensive|in nextjs|app router|supabase|i need|a |an |the )", " ", query)
    words = [w for w in re.findall(r"[a-z0-9]+", query) if len(w) > 3][:4]
    return "-".join(words) or path.stem.replace("_stream", "")


def wait_until_ready(path: Path, timeout: float = READY_TIMEOUT) -> dict | None:
    """Block until the file is complete and parseable, or give up.

    Two independent conditions, because either alone is misleading: a
    stable size can mean "still being written, just slowly", and a
    parseable file can still be missing worker steps the planner said it
    planned.
    """
    deadline = time.time() + timeout
    last_size, stable_since = -1, None

    while time.time() < deadline:
        try:
            size = path.stat().st_size
        except OSError:
            time.sleep(STABLE_POLL)
            continue

        if size != last_size:
            last_size, stable_since = size, time.time()
        elif stable_since and (time.time() - stable_since) >= STABLE_SECONDS:
            try:
                return load_architecture(path)
            except IncompleteExport as exc:
                # The planner planned more than the export contains. orch2
                # may still be writing workers; keep waiting.
                record("waiting_incomplete", file=path.name, detail=str(exc)[:160])
                stable_since = None
            except Exception as exc:  # noqa: BLE001 - malformed or not an architecture
                record("rejected_unparseable", file=path.name,
                       detail=f"{type(exc).__name__}: {exc}"[:160])
                return None
        time.sleep(STABLE_POLL)

    record("timeout_not_ready", file=path.name, waited=int(timeout))
    return None


def contract_summary(arch: dict) -> tuple[int, int, list[str]]:
    """(tickets with contracts, total files, ids without one) — the test
    for whether this architecture is code work at all."""
    by_id = {t["id"]: t for t in arch["tickets"]}
    owned: set[str] = set()
    contracted, files, missing = 0, 0, []
    for layer in build_order(arch["tickets"]):
        for tid in layer:
            try:
                c = contract_for_ticket(by_id[tid], [], owned)
            except ChangesetError:
                missing.append(tid)
                continue
            owned |= set(c.required_paths)
            contracted += 1
            files += len(c.required_paths)
    return contracted, files, missing


class ArchitectureHandler(FileSystemEventHandler):
    def __init__(self, args):
        self.args = args
        self.seen: set[str] = set()
        self.lock = threading.Lock()   # builds are serialised

    def seed(self, watch_dir: Path) -> int:
        """Mark everything already present as seen, WITHOUT building it."""
        existing = [p.name for p in watch_dir.glob("*.json")]
        self.seen.update(existing)
        return len(existing)

    # watchdog fires both created and modified; one handler covers both.
    def on_created(self, event):
        self._consider(event)

    def on_modified(self, event):
        self._consider(event)

    def _consider(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() != ".json":
            return
        with self.lock:
            if path.name in self.seen:
                return
            self.seen.add(path.name)
        threading.Thread(target=self.process, args=(path,), daemon=True).start()

    def process(self, path: Path) -> None:
        record("detected", file=path.name)
        arch = wait_until_ready(path)
        if arch is None:
            return

        tickets = arch["tickets"]
        if len(tickets) > self.args.max_tickets:
            record("skipped_too_large", file=path.name, tickets=len(tickets),
                   ceiling=self.args.max_tickets,
                   detail="raise --max-tickets to allow it")
            return

        name = slug_for(arch, path)

        # Route before importing. A document architecture fed to the
        # software factory still "plans" — fallback paths give every
        # ticket a file — so skipping on zero contracts never caught it.
        kind, why = deliverable_kind(tickets)
        if kind == "document":
            record("routed_document", file=path.name, name=name, tickets=len(tickets),
                   detail=why)
            if self.args.plan_only:
                record("plan_only", name=name, detail="skipping document build (--plan-only)")
                return
            with self.lock:
                self.build_documents(path, name)
            return

        contracted, files, missing = contract_summary(arch)
        if contracted == 0:
            record("skipped_not_code", file=path.name, tickets=len(tickets),
                   detail="no ticket yields a file contract; this is not a code architecture")
            return

        spec = SPECS_DIR / f"{name}.architecture.json"
        SPECS_DIR.mkdir(parents=True, exist_ok=True)
        spec.write_text(json.dumps(arch, indent=2), encoding="utf-8", newline="\n")
        record("imported", file=path.name, spec=spec.name, name=name,
               tickets=len(tickets), contracted=contracted, files=files,
               no_contract=len(missing))

        if self.args.plan_only:
            record("plan_only", name=name, detail="skipping build (--plan-only)")
            return

        # Serialised: concurrent builds would contend for the Supabase
        # port block, the npm cache and the target workspace.
        with self.lock:
            self.build(spec, name)

    def build(self, spec: Path, name: str) -> None:
        log_path = REPO_ROOT / "factory_runs" / f"watch_{name}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # -u: unbuffered. Redirected to a file, Python buffers stdout, and a
        # build two minutes in showed a 0-byte log — indistinguishable from
        # a hung one, which is exactly when someone opens it.
        cmd = [sys.executable, "-u", str(REPO_ROOT / "build_architecture.py"),
               str(spec), "--build", "--fresh", "--repo-name", name]
        record("build_start", name=name, log=log_path.name)
        started = time.time()
        with log_path.open("w", encoding="utf-8", newline="\n") as fh:
            proc = subprocess.run(cmd, cwd=REPO_ROOT, stdout=fh,
                                  stderr=subprocess.STDOUT, check=False)
        record("build_end", name=name, exit=proc.returncode,
               seconds=int(time.time() - started),
               detail=f"repo: factory_workspace/{name}")

    def build_documents(self, execution: Path, name: str) -> None:
        """The document factory reads the orch2 execution directly — its
        intake parses the orchestrator report format, not the imported
        architecture — authors a contract per ticket, then builds and
        gates each document. Passing ones land in drops/outbox/factory/."""
        log_path = REPO_ROOT / "factory_runs" / f"watch_{name}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-u", str(REPO_ROOT / "run_factory.py"), str(execution), "--build"]
        record("document_build_start", name=name, log=log_path.name)
        started = time.time()
        with log_path.open("w", encoding="utf-8", newline="\n") as fh:
            proc = subprocess.run(cmd, cwd=REPO_ROOT, stdout=fh,
                                  stderr=subprocess.STDOUT, check=False)
        record("document_build_end", name=name, exit=proc.returncode,
               seconds=int(time.time() - started),
               detail="documents: drops/outbox/factory/; escalations: factory_runs/<run>/escalated/")


def main() -> int:
    ap = argparse.ArgumentParser(description="Build orch2 architectures as they land")
    ap.add_argument("--watch-dir", type=Path, default=DEFAULT_WATCH_DIR)
    ap.add_argument("--plan-only", action="store_true",
                    help="import and report, never build")
    ap.add_argument("--max-tickets", type=int, default=15,
                    help="refuse plans larger than this (default 15)")
    ap.add_argument("--once", type=Path,
                    help="process a single execution file and exit")
    args = ap.parse_args()

    handler = ArchitectureHandler(args)

    if args.once:
        handler.process(args.once)
        return 0

    if not args.watch_dir.is_dir():
        print(f"Watch directory not found: {args.watch_dir}")
        return 1

    seeded = handler.seed(args.watch_dir)
    print(f"[watcher] watching {args.watch_dir}")
    print(f"[watcher] {seeded} existing execution(s) marked seen — not rebuilding them")
    print(f"[watcher] mode: {'PLAN ONLY' if args.plan_only else 'BUILD'}"
          f" | ceiling: {args.max_tickets} tickets")
    print("[watcher] dashboard: python factory_telemetry.py --serve")
    record("watch_start", dir=str(args.watch_dir), seeded=seeded,
           mode="plan-only" if args.plan_only else "build")

    observer = Observer()
    observer.schedule(handler, str(args.watch_dir), recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        record("watch_stop")
    observer.join()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
