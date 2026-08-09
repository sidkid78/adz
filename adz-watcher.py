#!/usr/bin/env python3
"""
Agentic Drop Zone (ADZ) Watcher
An autonomous background watcher script that monitors local file directories (drop zones)
and triggers specific agentic commands when matching file patterns are dropped.
"""

import os
import sys
import time
import yaml
import shutil
import logging
import subprocess
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import PatternMatchingEventHandler

# ANSI Terminal Colors for beautiful, distinct agent logging
COLORS = {
    "red": "\033[91m",
    "green": "\033[92m",
    "yellow": "\033[93m",
    "blue": "\033[94m",
    "magenta": "\033[95m",
    "cyan": "\033[96m",
    "white": "\033[97m",
    "reset": "\033[0m"
}

def setup_logging(log_file):
    """Sets up unified logging to file and stdout."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )

def color_print(text, color="white"):
    """Prints beautiful colored log lines to the terminal."""
    col = COLORS.get(color.lower(), COLORS["white"])
    print(f"{col}{text}{COLORS['reset']}")

class DropZoneHandler(PatternMatchingEventHandler):
    """Handles file events inside a specific agent drop zone."""
    
    def __init__(self, zone_config, global_settings):
        super().__init__(
            patterns=zone_config.get("patterns", ["*"]),
            ignore_directories=True,
            case_sensitive=False
        )
        self.zone_name = zone_config["name"]
        self.command_template = zone_config["command"]
        self.color = zone_config.get("color", "white")
        self.archive_dir = Path(global_settings.get("archive_dir", "./archive"))
        self.outbox_dir = Path(global_settings.get("outbox_dir", "./outbox"))
        
        # Ensure directories exist
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.outbox_dir.mkdir(parents=True, exist_ok=True)

    def wait_for_file_stability(self, file_path, delay=0.5, timeout=5):
        """Waits for file size to stabilize (ensuring full write/copy completed)."""
        file_path = Path(file_path)
        last_size = -1
        elapsed = 0
        while elapsed < timeout:
            try:
                current_size = file_path.stat().st_size
                if current_size == last_size and current_size > 0:
                    return True
                last_size = current_size
            except FileNotFoundError:
                return False
            time.sleep(delay)
            elapsed += delay
        return False

    def on_created(self, event):
        file_path = Path(event.src_path)
        logging.info(f"[{self.zone_name}] New file detected: {file_path.name}")
        
        # Wait until file finishes writing
        if not self.wait_for_file_stability(file_path):
            logging.error(f"[{self.zone_name}] File stability timeout or missing file: {file_path.name}")
            return
            
        self.execute_agentic_workflow(file_path)

    def execute_agentic_workflow(self, file_path):
        """Triggers the corresponding agent command and manages input/output lifecycle."""
        file_path = Path(file_path).resolve()
        file_name = file_path.name
        file_dir = file_path.parent
        
        # Format the command with dynamic variables
        cmd = self.command_template.format(
            file_path=str(file_path),
            file_name=file_name,
            file_dir=str(file_dir)
        )
        
        logging.info(f"[{self.zone_name}] Executing agentic command for: {file_name}")
        self.color_print(f"\n🚀 [{self.zone_name.upper()} ACTIVE] Triggering agent pattern...", self.color)
        self.color_print(f"Command: {cmd}\n", "yellow")
        
        # Execute command and capture output
        start_time = time.time()
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30
            )
            elapsed = time.time() - start_time
            
            # Print agent's output
            if result.stdout:
                self.color_print("--- Agent Output ---", self.color)
                print(result.stdout.strip())
            if result.stderr:
                self.color_print("--- Agent Stderr ---", "red")
                print(result.stderr.strip())
                
            self.color_print(f"--- Completed in {elapsed:.2f}s ---", self.color)
            
            # Save response artifact in the outbox
            zone_outbox = self.outbox_dir / self.zone_name
            zone_outbox.mkdir(parents=True, exist_ok=True)
            response_file = zone_outbox / f"{file_name}_response.txt"
            
            with open(response_file, "w") as f:
                f.write(f"=== AGENT {self.zone_name.upper()} EXECUTION METADATA ===\n")
                f.write(f"Trigger File: {file_name}\n")
                f.write(f"Execution Duration: {elapsed:.2f} seconds\n")
                f.write(f"Status Code: {result.returncode}\n\n")
                f.write("=== AGENT STDOUT ===\n")
                f.write(result.stdout or "(No stdout)")
                if result.stderr:
                    f.write("\n\n=== AGENT STDERR ===\n")
                    f.write(result.stderr)
            
            logging.info(f"[{self.zone_name}] Response saved: {response_file.name}")
            
        except subprocess.TimeoutExpired:
            logging.error(f"[{self.zone_name}] Command timed out after 30 seconds.")
            self.color_print("⚠️ Error: Command Timed Out", "red")
        except Exception as e:
            logging.error(f"[{self.zone_name}] Error executing command: {str(e)}")
            self.color_print(f"⚠️ Error: {str(e)}", "red")
            
        # Post-Processing: Archive the file so it doesn't trigger again
        self.archive_input_file(file_path)

    def archive_input_file(self, file_path):
        """Safely moves processed file to archive directory."""
        try:
            dest_path = self.archive_dir / file_path.name
            # If a file with the same name already exists in archive, add a timestamp
            if dest_path.exists():
                timestamp = int(time.time())
                dest_path = self.archive_dir / f"{file_path.stem}_{timestamp}{file_path.suffix}"
                
            shutil.move(str(file_path), str(dest_path))
            logging.info(f"[{self.zone_name}] Ingested file archived to: {dest_path.name}")
            self.color_print(f"📥 File archived to: {dest_path.name}", "white")
        except Exception as e:
            logging.error(f"[{self.zone_name}] Failed to archive file {file_path.name}: {str(e)}")

    def color_print(self, text, color="white"):
        color_print(text, color)


def main():
    config_path = Path("drops.yaml")
    if not config_path.exists():
        print(f"❌ Error: Config file drops.yaml not found in current directory.", file=sys.stderr)
        sys.exit(1)
        
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
        
    global_settings = config.get("global_settings", {})
    setup_logging(global_settings.get("log_file", "./adz_watcher.log"))
    
    logging.info("Initializing Agentic Drop Zones Watcher...")
    print("==================================================")
    color_print("📁 Starting Agentic Drop Zone (ADZ) background watcher", "blue")
    print("==================================================")
    
    observer = Observer()
    handlers = []
    
    for zone in config.get("drop_zones", []):
        zone_dir = Path(zone["directory"])
        zone_dir.mkdir(parents=True, exist_ok=True)
        
        handler = DropZoneHandler(zone, global_settings)
        observer.schedule(handler, str(zone_dir), recursive=False)
        handlers.append(handler)
        
        logging.info(f"Drop Zone Active: '{zone['name']}' -> Watching {zone_dir} for {zone['patterns']}")
        color_print(f"🟢 Registered zone '{zone['name']}' watching folder: {zone_dir}", "green")
        
    observer.start()
    color_print("\n🚀 Reactive directories initialized. Drag-and-drop to process files autonomously. Press Ctrl+C to terminate.", "yellow")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Shutting down Drop Zone Watcher...")
        observer.stop()
    observer.join()
    color_print("\n🛑 Watcher terminated.", "red")

if __name__ == "__main__":
    main()
