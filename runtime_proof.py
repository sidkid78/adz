"""
runtime_proof.py — boot the thing and ask whether it answers

THE GAP THIS CLOSES
-------------------
Every other gate in this factory is static. `tsc` typechecks, `vitest`
runs unit tests, `next build` compiles, `reachability` walks the import
graph. All of them passed on a repo whose `/` returned 404, because not
one of them ever started the server and made a request.

That defect survived six tickets, an integration gate, four repair
rounds, and a round of agents gaming the reachability check. A single
HTTP request would have caught it in the first thirty seconds.

"Does it compile" and "does it run" are different questions. This asks
the second one.

WHY NOT PLAYWRIGHT'S webServer
------------------------------
It is the right tool when the deliverable is a test suite: it owns the
process tree, polls for readiness, and tears down cleanly. But it means
adding @playwright/test and a browser download to every generated repo,
and it wants to run `next build` itself — which this factory has already
done as its own integration step, and which is the slowest thing in the
run.

So: the HTTP assertion in plain Python, no new dependency, against the
build that already exists. A screenshot is taken only if the repo
happens to have Playwright, because visual proof is a bonus and a 200 is
the actual gate.

WINDOWS
-------
No bash, no `kill -TERM -$PID` process groups, no `set -m` job control —
none of that works in Git Bash here, and a Next dev server spawns
children that outlive a naive kill. Teardown goes through
`taskkill /T /F` on Windows and `killpg` elsewhere. An orphaned server
holding a port is how the next run mysteriously fails.
"""

import contextlib
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

READY_TIMEOUT = 120          # seconds to wait for the server to answer at all
REQUEST_TIMEOUT = 15


def free_port() -> int:
    """Ask the OS for a port nothing is using.

    Never hardcode 3000. This machine routinely has several Next dev
    servers up (3000-3004 were all taken while this was written), and a
    gate that collides with the developer's own work reports a failure
    that is not about the code.
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _terminate(proc: subprocess.Popen) -> None:
    """Kill the server AND its children."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            # next start spawns workers; /T takes the tree, /F forces it.
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, check=False, timeout=30)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (OSError, subprocess.SubprocessError):
        pass
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=15)
    if proc.poll() is None:
        proc.kill()


def _get(url: str) -> tuple[int, str]:
    """(status, body). A 4xx/5xx is an answer, not an exception."""
    try:
        with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT) as r:
            return r.status, r.read(20000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(20000).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return 0, ""


def check_routes(repo_path: Path, routes: tuple[str, ...] = ("/",),
                 command: tuple[str, ...] = ("npm", "run", "start"),
                 screenshot: bool = True) -> tuple[bool, str]:
    """Start the built app, request each route, tear it down.

    Returns (passed, message). `routes` defaults to just "/" because the
    home page is the claim most worth checking and the one that was
    wrong; pass more when a build has other entry points.
    """
    port = free_port()
    env = {**os.environ, "PORT": str(port), "NODE_ENV": "production"}
    base = f"http://127.0.0.1:{port}"
    log: list[str] = []

    proc = subprocess.Popen(
        [*command, "--", "--port", str(port)],
        cwd=repo_path, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        shell=(os.name == "nt"),
        start_new_session=(os.name != "nt"),
    )

    try:
        # Poll rather than sleep. A fixed sleep is either flaky or slow,
        # and on a cold start it is both.
        deadline = time.time() + READY_TIMEOUT
        status = 0
        while time.time() < deadline:
            if proc.poll() is not None:
                out = (proc.stdout.read() if proc.stdout else "") or ""
                return False, f"server exited before answering (code {proc.returncode})\n{out[-2000:]}"
            status, _ = _get(base + "/")
            if status:
                break
            time.sleep(1)
        else:
            return False, f"server never answered on {base} within {READY_TIMEOUT}s"

        failures = []
        for route in routes:
            status, body = _get(base + route)
            log.append(f"  {route} -> HTTP {status}")
            if status != 200:
                failures.append(f"{route} returned HTTP {status}, expected 200")
            elif len(body.strip()) < 200:
                # A 200 that renders nothing is the other way this hides:
                # the route exists, the page is blank.
                failures.append(f"{route} returned 200 but only {len(body.strip())} bytes of HTML")

        shot = _screenshot(repo_path, base) if screenshot and not failures else ""

        if failures:
            return False, "\n".join(log + [""] + failures)
        return True, "\n".join(log + ([shot] if shot else []))
    finally:
        _terminate(proc)


def _screenshot(repo_path: Path, base: str) -> str:
    """Visual proof, captured with the FACTORY's Playwright.

    Not the target repo's. Screenshotting is a gate's job, so the
    browser belongs to the thing doing the gating — adding
    @playwright/test plus a 115MB browser download to every generated
    repo would make the product carry its inspector around forever.

    Never fatal: a missing browser costs the visual review, not the
    build.
    """
    out = repo_path / "artifacts" / "proof-of-work.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.goto(base + "/", wait_until="load", timeout=60000)
                # Client components hydrate after load; a screenshot taken
                # at `load` can catch a skeleton and report it as a blank
                # UI that nothing is actually wrong with.
                page.wait_for_timeout(1500)
                page.screenshot(path=str(out), full_page=True)
            finally:
                browser.close()
        if out.exists():
            return f"  proof: {out.relative_to(repo_path).as_posix()}"
    except Exception as exc:  # noqa: BLE001 - visual proof is a bonus, never a failure
        return f"  (no screenshot: {type(exc).__name__})"
    return ""


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    wanted = tuple(sys.argv[2:]) or ("/",)
    ok, message = check_routes(target, wanted)
    print(message)
    print("RUNTIME PROOF:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)
