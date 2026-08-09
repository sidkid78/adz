"""
sandbox_pool.py — The Sandbox Connector: pre-warmed devbox pool

Maintains a warm pool of E2B sandboxes with the repo and dependencies
already cloned/installed. Allocating one to a task is a queue pop
(near-instant) instead of a cold clone+install — we front-load that
cost at pool-fill time instead of paying it when an agent needs to run
*right now*. That's the "under 10 seconds" claim from the lesson text,
made concrete: it's fast because the slow part already happened.
"""

import concurrent.futures
import queue
import threading
from dataclasses import dataclass

from e2b import Sandbox

REPO_URL = "https://github.com/your-org/your-repo.git"
BASE_BRANCH = "main"
SETUP_COMMANDS = [
    "pip install -r requirements.txt -q",
    # add whatever system deps your repo needs here
]


@dataclass
class WarmSandbox:
    sbx: Sandbox
    repo_path: str = "/workspace/repo"


class SandboxPool:
    def __init__(self, pool_size: int = 3, sandbox_timeout: int = 900):
        self.pool_size = pool_size
        self.sandbox_timeout = sandbox_timeout
        self._pool: "queue.Queue[WarmSandbox]" = queue.Queue()
        self._fill(pool_size)

    def _provision_one(self) -> WarmSandbox:
        sbx = Sandbox.create(timeout=self.sandbox_timeout)
        sbx.commands.run(f"git clone -b {BASE_BRANCH} {REPO_URL} /workspace/repo")
        for cmd in SETUP_COMMANDS:
            sbx.commands.run(cmd, cwd="/workspace/repo")
        return WarmSandbox(sbx=sbx)

    def _fill(self, n: int) -> None:
        """Provision n sandboxes IN PARALLEL — clone+install is the slow
        part, no reason to pay that cost serially."""
        with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
            for warm in pool.map(lambda _: self._provision_one(), range(n)):
                self._pool.put(warm)

    def acquire(self) -> WarmSandbox:
        """Pop a pre-warmed sandbox. Falls back to cold provisioning only
        if the pool has been fully drained."""
        try:
            warm = self._pool.get_nowait()
        except queue.Empty:
            warm = self._provision_one()

        # Backfill in the background so the pool trends back to steady state.
        threading.Thread(target=self._backfill_one, daemon=True).start()
        return warm

    def _backfill_one(self) -> None:
        self._pool.put(self._provision_one())

    def release(self, warm: WarmSandbox) -> None:
        """Destroy a used sandbox. It never goes back in the pool — every
        task gets a genuinely fresh environment, never a prior task's
        leftovers (uncommitted files, stray processes, etc.)."""
        warm.sbx.kill()

    def shutdown(self) -> None:
        while not self._pool.empty():
            self._pool.get().sbx.kill()