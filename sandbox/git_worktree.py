"""
git_worktree.py — ephemeral Git worktrees for parallel agent exploration

Inside ONE warm sandbox's cloned repo, `git worktree add` creates a new
working directory checked out to its own branch, sharing the same .git
object store as the main clone. This is what lets several agents
explore different solutions to the same bug in parallel WITHOUT each
needing a full separate clone (or a full separate sandbox) — they share
history and objects, but never touch each other's files, because each
worktree has its own directory and its own branch.
"""

from dataclasses import dataclass
from e2b import Sandbox


@dataclass
class Worktree:
    branch: str
    path: str


def create_worktree(sbx: Sandbox, repo_path: str, branch: str) -> Worktree:
    worktree_path = f"/workspace/worktrees/{branch}"
    sbx.commands.run("mkdir -p /workspace/worktrees")

    # -b creates the branch fresh off the repo's current HEAD.
    result = sbx.commands.run(f"git worktree add -b {branch} {worktree_path}", cwd=repo_path)
    if result.exit_code != 0:
        raise RuntimeError(f"Failed to create worktree '{branch}': {result.stderr}")

    return Worktree(branch=branch, path=worktree_path)


def remove_worktree(sbx: Sandbox, repo_path: str, worktree: Worktree) -> None:
    sbx.commands.run(f"git worktree remove --force {worktree.path}", cwd=repo_path)