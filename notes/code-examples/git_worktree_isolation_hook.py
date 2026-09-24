#!/usr/bin/env python3
"""
git_worktree_isolation_hook.py
===============================
A production-grade Multi-Worktree Isolation Hook for Agent Harnesses.

When parallel sub-agents execute tasks simultaneously, working in a single directory
causes file locks, overwrite race conditions, and dirty git tree collisions.

This hook manages isolated Git Worktrees per agent:
1. Creates an isolated working directory & branch per agent (`/tmp/worktrees/{agent_id}`).
2. Directs the sub-agent's tool execution to its private worktree root.
3. Safely merges passing work back to the integration branch or cleans up on failure.
"""

import os
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional


class GitWorktreeManager:
    """Manages ephemeral Git worktrees for parallel agent isolation."""

    def __init__(self, repo_root: str, base_dir: Optional[str] = None):
        self.repo_root = os.path.abspath(repo_root)
        self.base_dir = base_dir or os.path.join(tempfile.gettempdir(), "agent_worktrees")
        os.makedirs(self.base_dir, exist_ok=True)

    def _run_cmd(self, cmd: List[str], cwd: Optional[str] = None) -> subprocess.CompletedProcess:
        target_dir = cwd or self.repo_root
        res = subprocess.run(cmd, cwd=target_dir, capture_output=True, text=True)
        return res

    def create_worktree(self, agent_id: str, base_branch: str = "HEAD") -> str:
        """
        Creates a dedicated worktree and branch for an agent.
        Branch format: agent/{agent_id}
        Directory format: {base_dir}/{agent_id}
        """
        worktree_path = os.path.join(self.base_dir, agent_id)
        branch_name = f"agent/{agent_id}"

        # Ensure directory does not already exist
        if os.path.exists(worktree_path):
            self.remove_worktree(agent_id, force=True)

        # Create new branch and worktree from base_branch
        cmd = ["git", "worktree", "add", "-b", branch_name, worktree_path, base_branch]
        res = self._run_cmd(cmd)

        if res.returncode != 0:
            # If branch already exists, attach to it without -b
            cmd_fallback = ["git", "worktree", "add", worktree_path, branch_name]
            res_fallback = self._run_cmd(cmd_fallback)
            if res_fallback.returncode != 0:
                raise RuntimeError(
                    f"Failed to create worktree for {agent_id}.\n"
                    f"Error: {res.stderr or res_fallback.stderr}"
                )

        print(f"🌲 [Worktree Manager] Created isolated worktree for '{agent_id}' at {worktree_path}")
        return worktree_path

    def remove_worktree(self, agent_id: str, delete_branch: bool = True, force: bool = True) -> bool:
        """Removes an agent's worktree and optionally deletes its git branch."""
        worktree_path = os.path.join(self.base_dir, agent_id)
        branch_name = f"agent/{agent_id}"

        if os.path.exists(worktree_path):
            cmd = ["git", "worktree", "remove"]
            if force:
                cmd.append("--force")
            cmd.append(worktree_path)
            self._run_cmd(cmd)

        if delete_branch:
            cmd_branch = ["git", "branch", "-D" if force else "-d", branch_name]
            self._run_cmd(cmd_branch)

        print(f"🧹 [Worktree Manager] Cleaned up worktree for '{agent_id}'")
        return True

    def merge_worktree_changes(self, agent_id: str, target_branch: str = "main") -> bool:
        """Merges passing worktree branch back into the target branch."""
        branch_name = f"agent/{agent_id}"
        
        # Checkout target branch in main repo and merge
        res_checkout = self._run_cmd(["git", "checkout", target_branch])
        if res_checkout.returncode != 0:
            print(f"❌ Failed to checkout {target_branch}: {res_checkout.stderr}")
            return False

        res_merge = self._run_cmd(["git", "merge", "--no-ff", "-m", f"Merge passing agent work: {agent_id}", branch_name])
        if res_merge.returncode != 0:
            print(f"❌ Merge collision for '{agent_id}': {res_merge.stderr}")
            # Abort merge to keep target clean
            self._run_cmd(["git", "merge", "--abort"])
            return False

        print(f"✅ [Worktree Manager] Successfully merged agent '{agent_id}' into '{target_branch}'")
        return True


def pre_agent_start_hook(agent_id: str, repo_root: str, base_branch: str = "HEAD") -> Dict[str, str]:
    """
    PRE_AGENT_START Harness Hook:
    Sets up isolated worktree and returns environment configuration for the sub-agent.
    """
    manager = GitWorktreeManager(repo_root=repo_root)
    worktree_path = manager.create_worktree(agent_id=agent_id, base_branch=base_branch)
    
    return {
        "AGENT_ID": agent_id,
        "AGENT_CWD": worktree_path,
        "STATUS": "ISOLATED"
    }


def post_agent_stop_hook(agent_id: str, repo_root: str, test_passed: bool, target_branch: str = "main") -> bool:
    """
    POST_AGENT_STOP Harness Hook:
    If validation passed, merges work back; otherwise discards dirty worktree.
    """
    manager = GitWorktreeManager(repo_root=repo_root)
    
    if test_passed:
        success = manager.merge_worktree_changes(agent_id=agent_id, target_branch=target_branch)
        manager.remove_worktree(agent_id=agent_id, delete_branch=success)
        return success
    else:
        print(f"⚠️ Validation failed for agent '{agent_id}'. Discarding dirty worktree without merge.")
        manager.remove_worktree(agent_id=agent_id, delete_branch=True)
        return False


# --- Self-Test Routine ---
def run_self_test():
    print("🧪 Testing Git Worktree Isolation Hook...")
    test_dir = tempfile.mkdtemp(prefix="worktree_test_repo_")

    try:
        # Initialize a dummy git repo
        subprocess.run(["git", "init"], cwd=test_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "TestAgent"], cwd=test_dir, check=True)
        subprocess.run(["git", "config", "user.email", "agent@test.com"], cwd=test_dir, check=True)

        # Create initial commit
        main_file = os.path.join(test_dir, "README.md")
        with open(main_file, "w") as f:
            f.write("# Main Branch Baseline\n")
        subprocess.run(["git", "add", "."], cwd=test_dir, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=test_dir, check=True)
        
        # Rename default branch to main
        subprocess.run(["git", "branch", "-M", "main"], cwd=test_dir, check=True)

        # 1. Test Pre-Agent Start (Worktree Creation)
        env = pre_agent_start_hook(agent_id="worker_01", repo_root=test_dir, base_branch="main")
        worktree_path = env["AGENT_CWD"]
        assert os.path.exists(worktree_path), "Worktree directory was not created!"

        # 2. Simulate Worker 01 making a change in its isolated worktree
        worker_file = os.path.join(worktree_path, "feature.txt")
        with open(worker_file, "w") as f:
            f.write("Feature built by worker_01\n")
        
        subprocess.run(["git", "add", "."], cwd=worktree_path, check=True)
        subprocess.run(["git", "commit", "-m", "Worker 01 completed feature"], cwd=worktree_path, check=True)

        # 3. Test Post-Agent Stop (Merge & Cleanup)
        merged = post_agent_stop_hook(agent_id="worker_01", repo_root=test_dir, test_passed=True, target_branch="main")
        assert merged, "Merge should have succeeded!"
        assert not os.path.exists(worktree_path), "Worktree directory should have been cleaned up!"

        # Verify change is present on main branch
        merged_file = os.path.join(test_dir, "feature.txt")
        assert os.path.exists(merged_file), "Feature file should exist on main after merge!"

        print("🎉 Git Worktree Isolation Hook self-tests passed successfully!")

    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


if __name__ == "__main__":
    run_self_test()
