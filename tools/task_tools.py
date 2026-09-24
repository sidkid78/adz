"""
task_tools.py — the shared task registry behind task_create/update/get/list

declarations.py already declares these four tools and maps them to
`self.task_registry`, but the module was missing, so importing
AgentToolsManager failed outright with ModuleNotFoundError. This is that
module, written to the interface the declarations already specify.

Why a file on disk rather than a dict in memory: the point of a *shared*
registry is that more than one agent can see it. A spawned sub-agent, a
second process, or a later run of the factory all need the same view of
what is done and what is still blocked. State that lives only inside one
AgentToolsManager instance cannot do that.

Dependency handling is deliberately read-only: task_get reports the
status of everything a task depends on and whether it is unblocked, but
nothing here refuses to let an agent proceed. Enforcement belongs to the
caller — the same split the repo already uses, where hooks and gates
decide and tools only report.
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

REGISTRY_FILENAME = ".adz_tasks.json"

VALID_STATUSES = ("pending", "in_progress", "blocked", "completed", "failed")
TERMINAL_STATUSES = ("completed",)


class TaskRegistry:
    """A small, file-backed task list shared across agents and processes."""

    def __init__(self, workspace_root: str = "."):
        self.workspace_root = Path(workspace_root).resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.workspace_root / REGISTRY_FILENAME
        # Guards read-modify-write cycles from concurrent tool calls in
        # one process (execute_batch and the HOTFIX racers both do this).
        self._lock = threading.Lock()

    # ---- persistence -------------------------------------------------
    def _load(self) -> Dict[str, dict]:
        if not self.registry_path.exists():
            return {}
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A corrupt registry must not take down every tool call that
            # touches it; start clean rather than raising into the agent.
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, tasks: Dict[str, dict]) -> None:
        self.registry_path.write_text(
            json.dumps(tasks, indent=2, sort_keys=True), encoding="utf-8", newline="\n"
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ---- tools -------------------------------------------------------
    def task_create(self, task_id: str, description: str,
                    assigned_agent: Optional[str] = None,
                    depends_on: Optional[List[str]] = None,
                    status: str = "pending") -> Dict:
        if not task_id:
            return {"success": False, "error": "task_id is required"}
        if status not in VALID_STATUSES:
            return {"success": False, "error": f"invalid status '{status}'; expected one of {VALID_STATUSES}"}

        with self._lock:
            tasks = self._load()
            if task_id in tasks:
                return {"success": False, "error": f"task '{task_id}' already exists"}
            tasks[task_id] = {
                "task_id": task_id,
                "description": description,
                "assigned_agent": assigned_agent,
                "depends_on": list(depends_on or []),
                "status": status,
                "result_summary": None,
                "created_at": self._now(),
                "updated_at": self._now(),
            }
            self._save(tasks)
        return {"success": True, "task_id": task_id, "status": status}

    def task_update(self, task_id: str, status: str,
                    result_summary: Optional[str] = None) -> Dict:
        if status not in VALID_STATUSES:
            return {"success": False, "error": f"invalid status '{status}'; expected one of {VALID_STATUSES}"}

        with self._lock:
            tasks = self._load()
            task = tasks.get(task_id)
            if task is None:
                return {"success": False, "error": f"task '{task_id}' not found"}
            task["status"] = status
            if result_summary is not None:
                task["result_summary"] = result_summary
            task["updated_at"] = self._now()
            self._save(tasks)
        return {"success": True, "task_id": task_id, "status": status}

    def task_get(self, task_id: str) -> Dict:
        tasks = self._load()
        task = tasks.get(task_id)
        if task is None:
            return {"success": False, "error": f"task '{task_id}' not found"}

        # Resolve dependency statuses so the agent can see WHY it is
        # blocked without making four more tool calls to find out.
        dependencies = []
        for dep_id in task.get("depends_on", []):
            dep = tasks.get(dep_id)
            dependencies.append({
                "task_id": dep_id,
                "status": dep["status"] if dep else "missing",
                "satisfied": bool(dep) and dep["status"] in TERMINAL_STATUSES,
            })

        return {
            "success": True,
            "task": task,
            "dependencies": dependencies,
            "unblocked": all(d["satisfied"] for d in dependencies),
        }

    def task_list(self, status_filter: Optional[str] = None) -> Dict:
        tasks = self._load()
        rows = list(tasks.values())
        if status_filter:
            rows = [t for t in rows if t["status"] == status_filter]
        rows.sort(key=lambda t: t.get("created_at") or "")
        counts: Dict[str, int] = {}
        for t in tasks.values():
            counts[t["status"]] = counts.get(t["status"], 0) + 1
        return {"success": True, "count": len(rows), "tasks": rows, "status_counts": counts}
