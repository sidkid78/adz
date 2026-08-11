"""
context_rules.py — Directory-Scoped Rules (progressive disclosure, part 1)

Mirrors .cursorrules / MDC-style scoped context: a rule file only gets
mounted into the prompt if its `scope` glob matches at least one of the
paths the agent is actually touching. A database rule file never bloats
a frontend agent's context, and vice versa.
"""

import fnmatch
from dataclasses import dataclass
from pathlib import Path

import yaml  # uv add pyyaml

RULES_DIR = Path(__file__).parent / "rules"


@dataclass
class Rule:
    scope: str  # glob pattern, e.g. "db/**" or "frontend/**"
    name: str
    content: str


def load_rules(rules_dir: Path = RULES_DIR) -> list[Rule]:
    rules = []
    for path in rules_dir.glob("*.md"):
        text = path.read_text()
        if text.startswith("---"):
            _, front_matter, body = text.split("---", 2)
            meta = yaml.safe_load(front_matter) or {}
        else:
            meta, body = {}, text
        rules.append(Rule(scope=meta.get("scope", "**"), name=path.stem, content=body.strip()))
    return rules


def rules_for_paths(rules: list[Rule], target_paths: list[str]) -> list[Rule]:
    """Only mount rules whose scope glob matches at least one target
    path. This IS the 'database agent never sees frontend rules'
    guarantee from the lesson, made literal."""
    return [r for r in rules if any(fnmatch.fnmatch(p, r.scope) for p in target_paths)]


def compile_context_block(rules: list[Rule]) -> str:
    if not rules:
        return ""
    sections = [f"## Rule: {r.name}\n{r.content}" for r in rules]
    return "# Directory-Scoped Rules (only ones relevant to the files you're touching)\n\n" + "\n\n".join(sections)