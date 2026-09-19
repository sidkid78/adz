"""
scout_plan_build.py — Scout-Plan-Build Partitioning (progressive
disclosure, part 2)

Pipeline:
  1. SCOUT (cheap, fast model) — sees only a file NAME listing, never
     file contents. Returns the small set of paths likely relevant.
  2. DISCLOSE — only those scouted files get read off disk. Everything
     else in the repo stays completely out of context.
  3. PLAN (expensive, frontier model) — reasons over the narrow slice,
     plus any directory-scoped rules that match those specific paths.
"""
from dotenv import load_dotenv 

load_dotenv()

import json
import os
from pathlib import Path

from google import genai

from .context_rules import load_rules, rules_for_paths, compile_context_block

SCOUT_MODEL = "gemini-3.5-flash-lite"
PLANNER_MODEL = "gemini-3.1-pro-preview"


def list_repo_files(root: Path, extensions=(".py", ".ts", ".tsx", ".sql")) -> list[str]:
    return [
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and p.suffix in extensions and ".git" not in p.parts and ".venv" not in p.parts and "__pycache__" not in p.parts
    ]


def scout(client: genai.Client, task: str, file_paths: list[str]) -> list[str]:
    """Cheap model picks relevant file paths from a plain NAME listing —
    it never sees contents. That's the entire token-footprint win of
    this step: filtering happens before the expensive read, not after."""
    listing = "\n".join(file_paths)
    interaction = client.interactions.create(
        model=SCOUT_MODEL,
        system_instruction=(
            "You are a code scout. Given a task and a file listing, return "
            "ONLY a JSON array of the file paths most likely relevant to the "
            "task. Return at most 6 paths. Respond with JSON only, no prose, "
            "no markdown fences."
        ),
        input=f"Task: {task}\n\nFile listing:\n{listing}",
    )
    try:
        return json.loads(interaction.output_text.strip())
    except json.JSONDecodeError:
        return []


def disclose(root: Path, relevant_paths: list[str], max_chars_per_file: int = 4000) -> str:
    """Reads ONLY the scouted files. Per-file truncation is a hard
    ceiling so one huge file can't blow the whole budget on its own."""
    blocks = []
    for rel_path in relevant_paths:
        full_path = root / rel_path
        if not full_path.exists():
            continue
        content = full_path.read_text(errors="ignore")[:max_chars_per_file]
        blocks.append(f"### {rel_path}\n```\n{content}\n```")
    return "\n\n".join(blocks)


def plan(client: genai.Client, task: str, root: Path, relevant_paths: list[str]) -> str:
    """Expensive model reasons ONLY over the disclosed slice, plus
    directory-scoped rules matching those specific paths."""
    matched_rules = rules_for_paths(load_rules(), relevant_paths)
    rules_block = compile_context_block(matched_rules)
    disclosed_code = disclose(root, relevant_paths)

    prompt = f"{rules_block}\n\n# Task\n{task}\n\n# Relevant files\n{disclosed_code}\n\nWrite a precise implementation plan."
    interaction = client.interactions.create(model=PLANNER_MODEL, input=prompt)
    return interaction.output_text.strip()


def print_context_economy(root: Path, all_paths: list[str], relevant_paths: list[str]) -> None:
    """Makes the 'context economy' claim concrete instead of theoretical."""
    full_dump_size = sum(
        len((root / p).read_text(errors="ignore")) for p in all_paths if (root / p).exists()
    )
    disclosed_size = len(disclose(root, relevant_paths))
    reduction = 100 - int(100 * disclosed_size / max(full_dump_size, 1))
    print(
        f"--- Context economy ---\n"
        f"Full repo dump would be:  ~{full_dump_size:,} chars\n"
        f"Scouted disclosure is:    ~{disclosed_size:,} chars\n"
        f"Reduction: ~{reduction}%"
    )


def scout_plan_build(task: str, repo_root: str) -> str:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    root = Path(repo_root)

    all_paths = list_repo_files(root)
    print(f"--- Repo has {len(all_paths)} candidate files ---")

    relevant_paths = scout(client, task, all_paths)
    print(f"--- Scout ({SCOUT_MODEL}) selected: {relevant_paths} ---")

    print_context_economy(root, all_paths, relevant_paths)

    implementation_plan = plan(client, task, root, relevant_paths)
    print(f"--- Plan ({PLANNER_MODEL}) ---\n{implementation_plan}")
    return implementation_plan


if __name__ == "__main__":
    scout_plan_build(
        task="Fix the off-by-one bug in pagination that returns one extra item on the last page.",
        repo_root=".",
    )