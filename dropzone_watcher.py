"""
dropzone_watcher.py — Agentic Drop Zone (ADZ) daemon

Watches drops/inbox/<zone>/ per drops/config/drops.yaml. When a file
lands, dispatches it to that zone's handler. This is the trigger
mechanism the decomposer was missing: instead of a human manually
deciding "this document needs splitting," dropping it into
drops/inbox/specs/ triggers decompose_into_tickets() automatically.

Only the 'specs' zone has a real handler wired below. 'finance' and
'transcribers' are reproduced from the blueprint's own sample config
as illustration — dropping a file there logs "no handler implemented"
rather than silently doing nothing, since no matching pipeline exists
yet for either in this codebase.

Run: uv run dropzone_watcher.py
"""

import subprocess
import time
from pathlib import Path

import yaml  # uv add pyyaml watchdog
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from decompose_into_tickets import decompose_into_tickets, write_tickets_to_outbox
from ingest_orchestrator_report import parse_tickets_from_json

CONFIG_PATH = Path(__file__).parent / "drops" / "config" / "drops.yaml"
ROOT = Path(__file__).parent


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text())["directories"]


# ---- Zone handlers: one function per zone name in drops.yaml --------
def handle_specs(file_path: Path, zone_config: dict) -> None:
    """A large planning document dropped here gets split into scoped
    tickets instead of being sent whole into the closed loop.

    .json files matching the orchestrator_workers report shape use the
    DETERMINISTIC parser (free, exact) — the decomposition already
    exists in the file's structure. Anything else falls back to the
    LLM decomposer, since flat markdown/text has no structure to parse."""
    if file_path.suffix == ".json":
        tickets = parse_tickets_from_json(file_path)
        source = "deterministic JSON parse"
    else:
        document_text = file_path.read_text(errors="ignore")
        tickets = decompose_into_tickets(document_text)
        source = "LLM decomposition"

    outbox_dir = ROOT / zone_config["outbox"]
    written = write_tickets_to_outbox(tickets, outbox_dir)

    print(f"[specs] {file_path.name} -> {len(written)} tickets ({source}) written to {outbox_dir}")
    for path in written:
        print(f"  - {path.name}")


ZONE_HANDLERS = {
    "specs": handle_specs,
}


def run_validators(zone_config: dict) -> None:
    for validator_cmd in zone_config.get("validators", []):
        result = subprocess.run(validator_cmd, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Validator failed: {validator_cmd}\n{result.stdout}{result.stderr}")


class DropZoneHandler(FileSystemEventHandler):
    def __init__(self, zone_name: str, zone_config: dict):
        self.zone_name = zone_name
        self.zone_config = zone_config
        self.handler_fn = ZONE_HANDLERS.get(zone_name)

    def on_created(self, event):
        if event.is_directory:
            return
        file_path = Path(event.src_path)
        print(f"--- Detected drop in '{self.zone_name}': {file_path.name} ---")

        if not self.handler_fn:
            print(f"No handler implemented for zone '{self.zone_name}' — skipping.")
            return

        try:
            self.handler_fn(file_path, self.zone_config)
            run_validators(self.zone_config)
        except Exception as e:
            print(f"Zone '{self.zone_name}' failed to process {file_path.name}: {e}")


def main():
    config = load_config()
    observer = Observer()

    for zone_name, zone_config in config.items():
        inbox_dir = ROOT / zone_config["inbox"]
        inbox_dir.mkdir(parents=True, exist_ok=True)
        observer.schedule(DropZoneHandler(zone_name, zone_config), str(inbox_dir), recursive=False)
        print(f"Watching {inbox_dir} (zone: {zone_name})")

    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()