#!/usr/bin/env python3
"""
Agentic Drop Zone (ADZ) Watcher Daemon
Monitors drop zones for incoming ticket prompts, triggers isolated ADW runs
in parallel Git worktrees, and routes synthesized outputs.
"""

import os
import sys
import time
import shutil
import logging
import subprocess
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

class ADZWatcherDaemon:
    def __init__(self, watch_dir="./drops/prompts", archive_dir="./archive", outbox_dir="./outbox"):
        self.watch_dir = Path(watch_dir)
        self.archive_dir = Path(archive_dir)
        self.outbox_dir = Path(outbox_dir)
        
        self.watch_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.outbox_dir.mkdir(parents=True, exist_ok=True)

    def process_ticket_drop(self, file_path: Path):
        """Processes an incoming prompt file dropped into the ADZ directory."""
        logging.info(f"📥 [ADZ Daemon] Processing dropped prompt file: {file_path.name}")
        
        # Read ticket content
        ticket_id = file_path.stem
        prompt_content = file_path.read_text(encoding="utf-8")
        
        # Dispatch to ADW Runner via subprocess
        cmd = f"python3 scratch/adw_runner.py"
        logging.info(f"🚀 [ADZ Daemon] Launching ADW Pipeline for '{ticket_id}'...")
        
        start_time = time.time()
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=60
            )
            duration = time.time() - start_time
            
            # Save output to outbox
            outbox_file = self.outbox_dir / f"{ticket_id}_result.json"
            outbox_content = f"""{{
  "ticket_id": "{ticket_id}",
  "duration_seconds": {duration:.2f},
  "status": "{'SUCCESS' if result.returncode == 0 else 'FAILURE'}",
  "stdout": {repr(result.stdout)},
  "stderr": {repr(result.stderr)}
}}"""
            outbox_file.write_text(outbox_content, encoding="utf-8")
            logging.info(f"✅ [ADZ Daemon] Outbox result written to: {outbox_file.name}")
            
        except Exception as e:
            logging.error(f"❌ [ADZ Daemon] Execution error on {ticket_id}: {str(e)}")
            
        # Archive original prompt file
        archive_target = self.archive_dir / file_path.name
        if archive_target.exists():
            archive_target = self.archive_dir / f"{file_path.stem}_{int(time.time())}{file_path.suffix}"
        shutil.move(str(file_path), str(archive_target))
        logging.info(f"📦 [ADZ Daemon] Prompt archived to: {archive_target.name}")

    def poll_once(self):
        """Polls watch_dir for any existing files."""
        files = [f for f in self.watch_dir.iterdir() if f.is_file() and not f.name.startswith(".")]
        for file_path in files:
            self.process_ticket_drop(file_path)
        return len(files)

def self_test():
    """Runs a quick self-test of the ADZ Watcher Daemon."""
    print("🧪 Testing ADZ Watcher Daemon...")
    daemon = ADZWatcherDaemon(watch_dir="/tmp/test_adz/drops", archive_dir="/tmp/test_adz/archive", outbox_dir="/tmp/test_adz/outbox")
    
    # Create test drop file
    test_drop = daemon.watch_dir / "T-102_drop.md"
    test_drop.write_text("Build HLS Video Player component", encoding="utf-8")
    
    processed_count = daemon.poll_once()
    assert processed_count == 1, "Daemon failed to detect drop file."
    assert not test_drop.exists(), "Original drop file was not archived."
    assert len(list(daemon.archive_dir.iterdir())) == 1, "File was not placed in archive dir."
    assert len(list(daemon.outbox_dir.iterdir())) == 1, "Result was not placed in outbox dir."
    
    # Cleanup test dirs
    shutil.rmtree("/tmp/test_adz")
    print("🎉 ADZ Watcher Daemon self-test PASSED successfully!")

if __name__ == "__main__":
    self_test()
