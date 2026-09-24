"""
clean_install.py — does this repo work for anyone but us?

THE LAYER THAT WAS MISSING
--------------------------
Every other gate in this factory runs against the workspace the factory
built: a directory with node_modules already populated, .env.local
already written, and whatever else accumulated during the run. That
workspace is the one environment where the repo is guaranteed to work.

So the factory has never once proved that a generated repo installs from
scratch. Every defect of the form "assumes something that is not in the
repo" is invisible to it:

  - a package imported in source but never added to package.json
  - a lockfile out of sync with the manifest
  - a file the build needs that .gitignore excludes from the commit
  - a run script that only works because of a global install

Three of today's defects were that shape — missing npm dependencies, no
`dev` script, Tailwind never installed — and each was found by a human
running the project, not by a gate.

This clones the repo's committed HEAD into a temp directory, installs
strictly from the lockfile, and builds. Nothing else is carried over.

WHY `git clone` AND NOT `cp`
----------------------------
Copying the directory copies the accumulated state, which is the thing
under suspicion. Cloning takes exactly what was committed — so a file
that was never added, or one .gitignore excludes, is simply absent, and
that absence is the finding.

WHY `npm ci` AND NOT `npm install`
----------------------------------
`npm install` will happily resolve a manifest whose lockfile disagrees,
updating the lockfile as it goes and hiding the drift. `npm ci` fails
when they disagree, which is the question being asked.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
        check=False, shell=(os.name == "nt"),
    )
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or ""))


def check(repo_path: Path, build: bool = True,
          timeout: int = 1800) -> tuple[bool, str]:
    """Clone committed HEAD, `npm ci`, build. Returns (passed, message)."""
    repo = repo_path.resolve()
    if not (repo / ".git").exists():
        return True, "not a git repo — nothing committed to test"
    if not (repo / "package-lock.json").exists():
        return False, ("no package-lock.json: `npm ci` cannot run and nothing "
                       "makes this repo's install reproducible")

    code, out = _run(["git", "ls-files", "--error-unmatch", "package-lock.json"],
                     repo, 120)
    if code != 0:
        return False, ("package-lock.json exists but is NOT committed, so a "
                       "clone of this repo cannot reproduce the install")

    log: list[str] = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        clone = Path(tmp) / "clone"
        code, out = _run(["git", "clone", "--depth", "1", "--no-hardlinks",
                          repo.as_uri(), str(clone)], repo, 300)
        if code != 0:
            return False, f"git clone of committed HEAD failed:\n{out[-1500:]}"
        log.append("  clone: committed HEAD only")

        # Anything the build needs that is not committed is absent here,
        # and that absence is the point.
        code, out = _run(["npm", "ci"], clone, timeout)
        if code != 0:
            return False, "\n".join(log + [
                "", ("npm ci FAILED in a clean clone — this repo does not "
                     "install from its own lockfile:"), out[-2500:]])
        log.append("  npm ci: ok")

        if build:
            code, out = _run(["npm", "run", "build"], clone, timeout)
            if code != 0:
                return False, "\n".join(log + [
                    "", ("npm run build FAILED in a clean clone — the build "
                         "depends on something not committed:"), out[-2500:]])
            log.append("  npm run build: ok")

        # A repo nobody can start is not finished, and this is the one
        # place the question is cheap to ask: the scripts are declared in
        # the manifest the clone just installed from.
        missing = _missing_scripts(clone)
        if missing:
            return False, "\n".join(log + [
                "", (f"package.json declares no {', '.join(missing)} script: "
                     f"a clean checkout has no documented way to run this project")])
        log.append("  scripts: dev/build/start present")

    return True, "\n".join(log)


def _missing_scripts(repo: Path) -> list[str]:
    import json
    try:
        scripts = json.loads((repo / "package.json").read_text(encoding="utf-8")).get("scripts", {})
    except (OSError, json.JSONDecodeError):
        return []
    return [name for name in ("dev", "build", "start") if name not in scripts]


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    no_build = "--no-build" in sys.argv
    if not shutil.which("npm") and os.name != "nt":
        print("npm not on PATH — skipped")
        raise SystemExit(0)
    ok, message = check(target, build=not no_build)
    print(message)
    print("CLEAN INSTALL:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)
