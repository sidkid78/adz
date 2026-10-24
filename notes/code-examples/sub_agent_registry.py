#!/usr/bin/env python3
"""
Automated Sub-Agent Registry
Inspects generated sub-agent capabilities from .claude/agents/ and dynamically selects
the optimal worker sub-agent for an incoming ticket, or triggers generation of a new one if threshold is missed.
"""

import os
import sys
import re
import json
import yaml
import logging
from pathlib import Path

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

class SubAgentRegistry:
    def __init__(self, agents_dir="/tmp/claude/agents", threshold=0.3):
        self.agents_dir = Path(agents_dir)
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        self.threshold = threshold
        self.registry = {}
        self.refresh_registry()

    def refresh_registry(self):
        """Scans agents_dir for markdown agent configurations and parses frontmatter."""
        self.registry = {}
        for agent_file in self.agents_dir.glob("*.md"):
            agent_data = self._parse_agent_file(agent_file)
            if agent_data:
                self.registry[agent_data["name"]] = agent_data
        logging.info(f"🔍 Registry updated. Discovered {len(self.registry)} active sub-agent(s).")

    def _parse_agent_file(self, file_path):
        """Extracts YAML frontmatter from agent configuration file."""
        try:
            content = file_path.read_text(encoding="utf-8")
            frontmatter_match = re.search(r"^---\n(.*?)\n---", content, re.DOTALL)
            if not frontmatter_match:
                return None
            
            metadata = yaml.safe_load(frontmatter_match.group(1))
            metadata["file_path"] = str(file_path)
            return metadata
        except Exception as e:
            logging.error(f"Failed to parse agent file {file_path}: {e}")
            return None

    def match_sub_agent(self, ticket_description, target_files=None):
        """
        Scores registered sub-agents against the ticket description and target files.
        Returns (best_agent, match_score) or (None, 0.0) if below threshold.
        """
        if not self.registry:
            return None, 0.0

        ticket_tokens = set(re.findall(r"\w+", ticket_description.lower()))
        target_files = target_files or []

        best_agent = None
        best_score = 0.0

        for name, meta in self.registry.items():
            score = 0.0
            description_tokens = set(re.findall(r"\w+", meta.get("description", "").lower()))
            
            # Token overlap score
            if ticket_tokens and description_tokens:
                overlap = len(ticket_tokens & description_tokens)
                token_score = overlap / min(len(ticket_tokens), len(description_tokens))
                score += token_score * 0.6

            # Path restriction match score
            path_restrictions = meta.get("path_restrictions", [])
            if target_files and path_restrictions:
                path_match = False
                for tf in target_files:
                    for pr in path_restrictions:
                        clean_pr = pr.replace("*", "")
                        if clean_pr in tf:
                            path_match = True
                            break
                if path_match:
                    score += 0.4

            if score > best_score:
                best_score = score
                best_agent = meta

        logging.info(f"🎯 Matching complete. Best agent: {best_agent['name'] if best_agent else 'None'} (Score: {best_score:.2f})")
        
        if best_score >= self.threshold:
            return best_agent, best_score
        else:
            return None, best_score

    def get_or_create_worker(self, ticket_id, description, target_files=None):
        """
        Selects an existing worker sub-agent, or dynamically triggers meta-agent creation if no match exists.
        """
        self.refresh_registry()
        best_agent, score = self.match_sub_agent(description, target_files)

        if best_agent:
            logging.info(f"✅ Found existing worker '{best_agent['name']}' (Match Confidence: {score*100:.1f}%)")
            return {
                "action": "SELECTED_EXISTING",
                "agent_name": best_agent["name"],
                "file_path": best_agent["file_path"],
                "model_tier": best_agent.get("model_tier", "workhorse-sonnet"),
                "score": score
            }
        else:
            logging.info(f"⚠️ No matching sub-agent above threshold ({self.threshold}). Dynamically instantiating new specialized worker...")
            new_agent_name = f"{ticket_id.lower().replace('-', '_')}_specialist"
            agent_file = self.agents_dir / f"{new_agent_name}.md"
            
            agent_content = f"""---
name: "{new_agent_name}"
description: "Specialized worker automatically generated for ticket {ticket_id}: {description[:50]}"
tools: ["view_file", "write_to_file", "execute_command"]
model_tier: "workhorse-sonnet"
path_restrictions: {json.dumps(target_files or [])}
max_capacity_threshold: 0.85
---

# Role: {new_agent_name}
Automated specialist assigned to ticket {ticket_id}.

## Target Files
{json.dumps(target_files or [])}

## Directives
1. Read target files carefully.
2. Execute edits to resolve: {description}.
3. Run validation gates before finishing.
"""
            agent_file.write_text(agent_content, encoding="utf-8")
            self.refresh_registry()
            
            return {
                "action": "GENERATED_NEW",
                "agent_name": new_agent_name,
                "file_path": str(agent_file),
                "model_tier": "workhorse-sonnet",
                "score": 1.0
            }


if __name__ == "__main__":
    print("🧪 Testing Automated Sub-Agent Registry...")
    registry = SubAgentRegistry()
    
    # Pre-populate a test agent
    test_agent_path = Path("/tmp/claude/agents/db_migration_worker.md")
    test_agent_path.parent.mkdir(parents=True, exist_ok=True)
    test_agent_path.write_text("""---
name: "db_migration_worker"
description: "Specialized worker for database schema migrations, SQL queries, and lock resolution."
tools: ["sqlite3", "execute_command", "view_file"]
model_tier: "workhorse-sonnet"
path_restrictions: ["db/*", "migrations/*"]
---
# Role: Database Specialist
""", encoding="utf-8")

    # Case 1: Match existing
    res1 = registry.get_or_create_worker("T-201", "Resolve database schema migration lock issue", target_files=["db/migration_01.sql"])
    assert res1["action"] == "SELECTED_EXISTING"
    assert res1["agent_name"] == "db_migration_worker"

    # Case 2: Auto-generate new when no match exists
    res2 = registry.get_or_create_worker("T-202", "Update frontend UI canvas colors in React component", target_files=["src/components/Canvas.tsx"])
    assert res2["action"] == "GENERATED_NEW"
    assert res2["agent_name"] == "t_202_specialist"

    print("🎉 Automated Sub-Agent Registry self-tests PASSED successfully!")
