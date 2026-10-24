#!/usr/bin/env python3
"""
Agent Expert Memory Engine
Manages localized domain mental models (.claude/expertise/{domain}.yaml)
and executes the self-improve diff analysis loop to persist learned insights across sessions.
"""

import os
import sys
import yaml
import time
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

class AgentExpertMemoryEngine:
    """Manages domain expertise files and updates them via git diff analysis."""

    def __init__(self, expertise_dir: str = ".claude/expertise"):
        self.expertise_dir = Path(expertise_dir)
        self.expertise_dir.mkdir(parents=True, exist_ok=True)

    def load_domain_expertise(self, domain: str) -> Dict[str, Any]:
        """Loads a domain mental model file or creates a default template."""
        file_path = self.expertise_dir / f"{domain}.yaml"
        if not file_path.exists():
            default_model = {
                "domain": domain,
                "version": 1,
                "last_updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "core_files": [],
                "architectural_patterns": [],
                "known_gotchas": [],
                "successful_workflows": []
            }
            self.save_domain_expertise(domain, default_model)
            return default_model

        with open(file_path, "r") as f:
            return yaml.safe_load(f) or {}

    def save_domain_expertise(self, domain: str, data: Dict[str, Any]) -> Path:
        """Saves updated domain expertise back to disk."""
        file_path = self.expertise_dir / f"{domain}.yaml"
        data["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        data["version"] = data.get("version", 1) + 1

        with open(file_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

        logging.info(f"🧠 Mental model for domain '{domain}' updated: {file_path}")
        return file_path

    def analyze_git_diff(self, repo_dir: str = ".") -> str:
        """Extracts current git status/diff summary for self-improvement analysis."""
        try:
            result = subprocess.run(
                ["git", "diff", "HEAD~1"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
            
            # Fallback to unstaged diff if no commit difference
            result_unstaged = subprocess.run(
                ["git", "diff"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                timeout=10
            )
            return result_unstaged.stdout.strip() or "(No git diff available)"
        except Exception as e:
            return f"(Diff capture failed: {str(e)})"

    def execute_self_improve(self, domain: str, task_summary: str, repo_dir: str = ".") -> Dict[str, Any]:
        """Runs the closed-loop self-improvement pass, distilling diff insights into the mental model."""
        logging.info(f"🔄 Executing self-improvement analysis for domain '{domain}'...")
        expertise = self.load_domain_expertise(domain)
        git_diff = self.analyze_git_diff(repo_dir)

        # Distill key insights from diff and task execution
        new_gotchas = []
        new_core_files = set(expertise.get("core_files", []))

        # Extract touched files from diff
        for line in git_diff.splitlines():
            if line.startswith("+++ b/"):
                touched_file = line.replace("+++ b/", "").strip()
                if touched_file and touched_file != "/dev/null":
                    new_core_files.add(touched_file)

        # Distill new gotchas/learnings from task summary or error logs
        if "error" in task_summary.lower() or "repair" in task_summary.lower():
            new_gotchas.append(f"Retry insight ({time.strftime('%Y-%m-%d')}): {task_summary}")
        else:
            workflow_entry = f"Verified workflow ({time.strftime('%Y-%m-%d')}): {task_summary}"
            if workflow_entry not in expertise.get("successful_workflows", []):
                expertise.setdefault("successful_workflows", []).append(workflow_entry)

        for gotcha in new_gotchas:
            if gotcha not in expertise.get("known_gotchas", []):
                expertise.setdefault("known_gotchas", []).append(gotcha)

        expertise["core_files"] = sorted(list(new_core_files))
        self.save_domain_expertise(domain, expertise)
        return expertise

def self_test():
    """Runs verification tests for the Agent Expert Memory Engine."""
    print("🧪 Testing Agent Expert Memory Engine...")
    test_dir = "/tmp/test_expertise"
    engine = AgentExpertMemoryEngine(expertise_dir=test_dir)

    # 1. Test Load/Create Default
    model = engine.load_domain_expertise("database_expert")
    assert model["domain"] == "database_expert"
    assert "version" in model

    # 2. Test Self-Improvement Loop
    updated_model = engine.execute_self_improve(
        domain="database_expert",
        task_summary="Fixed migration deadlock by reordering transaction statements"
    )
    assert len(updated_model["successful_workflows"]) > 0

    print("🎉 Agent Expert Memory Engine self-tests PASSED successfully!")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        self_test()
    else:
        self_test()
