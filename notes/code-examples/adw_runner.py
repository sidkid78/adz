#!/usr/bin/env python3
"""
AI Developer Workflow (ADW) Runner: Scout -> Plan -> Build -> Validate
Implements the R&D Framework (Reduce & Delegate) for context-managed autonomous coding.
"""

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class ADWConfig:
    spec_dir: Path = Path("specs")
    max_repair_attempts: int = 3
    scout_model: str = "claude-3-5-haiku"
    plan_model: str = "claude-3-7-sonnet"
    build_model: str = "claude-3-7-sonnet"
    validation_command: str = "npm test"  # or 'just test', 'pytest', etc.


@dataclass
class WorkflowState:
    ticket_id: str
    user_prompt: str
    scouted_files: List[str] = field(default_factory=list)
    spec_path: Optional[Path] = None
    repair_attempts: int = 0
    is_validated: bool = False


class ADWRunner:
    def __init__(self, config: ADWConfig):
        self.config = config
        self.config.spec_dir.mkdir(parents=True, exist_ok=True)

    def run(self, ticket_id: str, user_prompt: str) -> bool:
        print(f"🚀 [ADW Pipeline] Starting Ticket {ticket_id}")
        state = WorkflowState(ticket_id=ticket_id, user_prompt=user_prompt)

        # -------------------------------------------------------------
        # PHASE 1: SCOUT (Delegate & Reduce)
        # Fast model pass to identify target files without polluting main context
        # -------------------------------------------------------------
        print("\n🔍 Phase 1: Scouting codebase (Delegated Search)...")
        state.scouted_files = self._scout_phase(state.user_prompt)
        print(f"   Scouted target files ({len(state.scouted_files)}): {state.scouted_files}")

        # -------------------------------------------------------------
        # PHASE 2: PLAN (Reduce & Persist)
        # Generate specification document and write to specs/ directory
        # -------------------------------------------------------------
        print("\n📋 Phase 2: Generating Specification (Context Reduction)...")
        state.spec_path = self._plan_phase(state)
        print(f"   Specification persisted to: {state.spec_path}")

        # -------------------------------------------------------------
        # PHASE 3 & 4: BUILD & VALIDATE (Context Reset + RVR Repair Loop)
        # Launch fresh builder instance primed ONLY with spec + target files
        # -------------------------------------------------------------
        print("\n🔨 Phase 3 & 4: Building & Deterministic Validation Loop...")
        
        while state.repair_attempts < self.config.max_repair_attempts:
            # Context Reset: Fresh builder instance per attempt
            print(f"   -> Build Attempt {state.repair_attempts + 1}/{self.config.max_repair_attempts}")
            self._build_phase(state)

            # Deterministic Code Gate (Zero-Token Execution)
            success, error_log = self._validate_phase()
            if success:
                print("   ✅ Deterministic validation passed!")
                state.is_validated = True
                break

            print(f"   ❌ Validation failed (Attempt {state.repair_attempts + 1})")
            state.repair_attempts += 1
            
            # Feed raw stderr back into next turn for RVR self-repair
            state.user_prompt = (
                f"Validation failed during build.\nError Output:\n{error_log}\n"
                "Fix the issues noted in the error log while adhering strictly to "
                f"the spec at {state.spec_path}."
            )

        # -------------------------------------------------------------
        # PHASE 5: TEARDOWN & AUDIT
        # -------------------------------------------------------------
        if state.is_validated:
            print(f"\n🎉 [ADW Pipeline] Ticket {ticket_id} COMPLETED SUCCESSFULLY.")
            return True
        else:
            print(f"\n💥 [ADW Pipeline] Ticket {ticket_id} EXHAUSTED REPAIR ATTEMPTS. Escalating to human review.")
            return False

    def _scout_phase(self, prompt: str) -> List[str]:
        """Simulates/Triggers scout agent pass using fast model tier."""
        # In actual harness, calls fast model CLI (e.g. claude -m claude-3-5-haiku)
        # Returns concise array of relevant file paths
        return ["src/features/feed/VideoFeed.tsx", "src/features/feed/types.ts"]

    def _plan_phase(self, state: WorkflowState) -> Path:
        """Generates structured spec and writes to disk, decoupling planning from building."""
        spec_file = self.config.spec_dir / f"{state.ticket_id}_spec.md"
        spec_content = (
            f"# Feature Specification: {state.ticket_id}\n\n"
            f"## User Goal\n{state.user_prompt}\n\n"
            f"## Target Files\n" + "\n".join(f"- {f}" for f in state.scouted_files) + "\n\n"
            f"## Architectural Boundaries\n"
            f"- Maintain Vertical Slice isolation in src/features/feed/\n"
            f"- Do not import or modify global utils/\n"
            f"- Ensure all prop types adhere to strict TypeScript interfaces\n"
        )
        spec_file.write_text(spec_content, encoding="utf-8")
        return spec_file

    def _build_phase(self, state: WorkflowState) -> None:
        """Launches isolated builder instance primed ONLY with spec + target files."""
        # In actual harness, executes builder agent with isolated prompt context:
        # e.g., claude --print "Read {state.spec_path} and implement modifications."
        pass

    def _validate_phase(self) -> Tuple[bool, str]:
        """Runs zero-token deterministic code gate (linter/tests/typecheck)."""
        try:
            res = subprocess.run(
                self.config.validation_command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=60
            )
            if res.returncode == 0:
                return True, ""
            return False, res.stderr or res.stdout
        except Exception as e:
            return False, str(e)


if __name__ == "__main__":
    runner = ADWRunner(ADWConfig(validation_command="echo 'Typescript & Jest check passing'"))
    runner.run("T-101", "Add HLS video streaming support with telemetry to video feed card.")
