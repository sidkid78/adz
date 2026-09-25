"""
greenfield.py — the target repo: scaffold it, then let its own toolchain judge it

THE SHIFT THIS FILE REPRESENTS
------------------------------
Up to now the factory's unit of work was "one artifact file" and its gate
was "checks over that file". For a software factory both are wrong:

  - real code is a TREE (a migration, its types, the tool that imports
    them, the test that exercises it), not a file;
  - the real gate is the repo's OWN toolchain. `tsc --noEmit` knows
    whether planning_engine actually compiles against the types
    mcp_init produced. No hand-written check can approximate that, and
    none needs to.

So the factory scaffolds a real git repo, writes changesets into it, and
asks tsc and vitest whether they are acceptable. That is the "Code costs
zero tokens and is the most reliable actor" principle from notes.md,
applied literally: the compiler is the reviewer.

STAGED GATING
-------------
`npm install` and a full framework build are slow and network-bound.
Running them per ticket per retry would dominate the wall clock, so:

  install        once, at scaffold time
  per-ticket     typecheck (+ this ticket's tests) — seconds
  integration    the full suite, once, after every ticket has landed

which is what real CI does, and for the same reason.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = REPO_ROOT / "factory_workspace"

# Versions track what the project owner sets, and the owner keeps them
# current for SECURITY reasons — Next 15 carries known CVEs, so this
# pins forward to 16.x rather than back.
#
# Reproducibility comes from committing package-lock.json and installing
# from it, NOT from freezing these ranges. An earlier version of this
# file pinned exact versions after misreading a deliberate upgrade as
# accidental drift; that traded away security patches to solve a problem
# the lockfile already solves.
#
# Next 16 defaults to Turbopack, which changes two things the factory
# depends on: a `webpack` key in next.config makes the build refuse to
# start, and `next build` no longer typechecks the generated route
# validator — hence the separate "check:routes" script below.
PACKAGE_JSON = {
    "name": "pm-mcp-server",
    "version": "0.1.0",
    "private": True,
    "type": "module",
    "scripts": {
        # The obvious ones. The scaffold used to ship only gate scripts,
        # so a finished project had no way to run — `npm run dev` failed
        # and you had to know to type `npx next dev`. A factory that
        # produces an app nobody can start has not finished the job.
        "dev": "next dev",
        "build": "next build",
        "start": "next start",
        "typecheck": "tsc -p tsconfig.check.json --noEmit",
        "test": "vitest run --passWithNoTests",
        # Next 16 generates .next/types/validator.ts but does not
        # typecheck it during `next build`. Without these the route
        # contracts (handler signatures, `params` being a Promise) are
        # generated and then never checked.
        #
        # Two scripts, not one with `&&`: npm runs scripts through the
        # platform shell, and on Windows that is PowerShell 5.1, where
        # `&&` is a parser error. A chained script passes on CI and fails
        # on a developer laptop for reasons unrelated to the code.
        "typegen": "next typegen",
        "check:routes": "tsc -p tsconfig.json --noEmit",
        # `pregate`/`gate`, not `typecheck && test`, for the same reason.
        # npm runs `pre<name>` before `<name>` itself and stops if it
        # exits nonzero, so the sequencing comes from npm rather than
        # from a shell operator that PowerShell cannot parse. Verified
        # by breaking a type on purpose: pregate fails, vitest never
        # runs, `npm run gate` exits 1.
        "pregate": "npm run typecheck",
        "gate": "npm run test",
    },
    "dependencies": {
        "@supabase/supabase-js": "^2.58.0",
        "fastmcp": "^3.19.0",
        "next": "^16.3.5",
        "react": "^19.3.0",
        "react-dom": "^19.3.0",
        "zod": "^3.25.76",
    },
    # Tailwind is toolchain, so it belongs here: a ticket cannot add it
    # (write_files refuses package.json), and the architectures reaching
    # this factory assume it — the frontend expert role is literally
    # "frontend_development_tailwind_next_js". Without it the generated
    # components render as unstyled HTML with page-sized SVG icons, and
    # no gate notices: tsc, vitest and next build are all perfectly happy
    # with class names that resolve to nothing.
    "devDependencies": {
        "@tailwindcss/postcss": "^4.3.3",
        "tailwindcss": "^4.3.3",
        "@types/node": "^22.18.11",
        "@types/react": "^19.3.0",
        "@types/react-dom": "^19.3.0",
        "typescript": "^5.9.3",
        "vitest": "^5.0.1",
    },
}

TSCONFIG = {
    "compilerOptions": {
        "target": "ES2022",
        # DOM is required by the Next.js/React portion of the architecture;
        # the MCP server half only needs ES2022 but the two share a tsconfig.
        "lib": ["ES2022", "DOM", "DOM.Iterable"],
        # Next rewrites tsconfig on its first build ("mandatory changes
        # were made to your tsconfig.json"): jsx->preserve,
        # isolatedModules->true, plus its plugin. Setting them up front
        # keeps the config stable instead of mutating mid-run, which
        # would make one ticket's gate differ from the next's for
        # reasons unrelated to the code.
        "jsx": "preserve",
        "isolatedModules": True,
        "incremental": True,
        "plugins": [{"name": "next"}],
        "module": "ESNext",
        "moduleResolution": "bundler",
        "strict": True,
        "noEmit": True,
        "esModuleInterop": True,
        "skipLibCheck": True,
        "forceConsistentCasingInFileNames": True,
        "resolveJsonModule": True,
        "types": ["node", "vitest/globals"],
        "baseUrl": ".",
        "paths": {"@/*": ["src/*"]},
    },
    "include": ["src/**/*.ts", "src/**/*.tsx", "tests/**/*.ts"],
    # NOT excluding ".next" here. Next generates route-contract types
    # under .next/types and adds them to `include`; this config is what
    # the "check:routes" script typechecks them through.
    #
    # On Next 15 `next build` ran that check itself. Next 16 with
    # Turbopack does NOT — it generates the validator and never
    # typechecks it, so `next build` went green with a route whose
    # `params` had the wrong type. Hence check:routes as its own step.
    "exclude": ["node_modules"],
}

# The per-ticket gate needs the opposite tradeoff: a pure SOURCE check
# that means the same thing whether or not a build has run. Two configs,
# one per job, instead of one config that quietly changes meaning.
TSCONFIG_CHECK = {
    "extends": "./tsconfig.json",
    "exclude": ["node_modules", ".next"],
}

VITEST_CONFIG = """\
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    globals: true,
    environment: 'node',
    include: ['tests/**/*.test.ts'],
  },
});
"""

GITIGNORE = "node_modules/\ndist/\n.next/\nout/\n.env\n.env.local\n*.log\n"

# The baseline must be GREEN. An empty repo fails `tsc` outright
# (TS18003: no inputs found), which would make the first ticket inherit a
# red gate and have to fix a failure it did not cause. Seeding a real
# entry point and a real test means every later gate result is
# attributable to the ticket that just ran. Both files are owned by
# tickets in the plan and get overwritten by them.
SEED_FILES = {
    # Framework structure, not a ticket's job. The App Router refuses to
    # build ANY page without a root layout ("page.tsx doesn't have a root
    # layout"), so without this every frontend ticket would fail the
    # integration gate for a reason it did not cause.
    "next.config.mjs": """\
/** @type {import('next').NextConfig} */
const nextConfig = {
  // Turbopack is the default bundler from Next 16. A `webpack` key here
  // makes the build refuse to start ("using Turbopack, with a `webpack`
  // config and no `turbopack` config"), so the extensionAlias trick that
  // worked on 15 is gone. Relative imports are written WITHOUT a .js
  // extension instead, which is what moduleResolution "bundler" expects
  // and what Turbopack resolves natively.
  turbopack: {},
};

export default nextConfig;
""",
    "postcss.config.mjs": """\
const config = {
  plugins: { "@tailwindcss/postcss": {} },
};

export default config;
""",
    "src/app/globals.css": """\
@import "tailwindcss";
""",
    "src/app/layout.tsx": """\
import './globals.css';

import type { ReactNode } from 'react';

export const metadata = {
  title: 'Project Management MCP',
  description: 'Agentic project management dashboard',
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
""",
    "src/index.ts": """\
/**
 * Entry point. Replaced by the MCP server initialisation ticket.
 */
export const PLACEHOLDER = true;
""",
    "tests/smoke.test.ts": """\
import { describe, expect, it } from 'vitest';

import { PLACEHOLDER } from '../src/index';

describe('scaffold', () => {
  it('compiles and runs the test harness', () => {
    expect(PLACEHOLDER).toBe(true);
  });
});
""",
}

# Directories the architecture's own file paths imply. Creating them up
# front means a ticket that writes src/tools/x.ts doesn't have to think
# about mkdir, and keeps the tree recognisable while it is still empty.
SKELETON_DIRS = [
    "src/types", "src/lib", "src/tools", "src/prompts", "src/logic",
    "src/hooks", "src/components/dashboard", "src/app",
    "supabase/migrations", "tests",
]


@dataclass
class GateResult:
    passed: bool
    transcript: str
    failed_command: str | None = None


def _pid_alive(pid: int) -> bool:
    """Is this PID still running? Used to reclaim a lock left by a build
    that was killed before it could release one."""
    if os.name == "nt":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, check=False,
        )
        return str(pid) in (out.stdout or "")
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def force_wipe(path: Path, attempts: int = 3) -> list[str]:
    """Delete a directory tree, returning whatever could not be removed.

    Windows keeps a handle on files a running process has open, and a
    read-only bit is enough to stop rmtree on its own. Both are ordinary
    after an interrupted build, so this clears the read-only bit, retries,
    and — crucially — REPORTS what survived instead of swallowing it.

    node_modules is deliberately included: a stale tree beside a fresh
    package.json is how a build ends up typechecking against the wrong
    versions.
    """
    import stat

    def clear_readonly(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    for attempt in range(attempts):
        shutil.rmtree(path, onexc=clear_readonly)
        if not path.exists():
            return []
        if attempt < attempts - 1:
            time.sleep(1.0)  # give a closing process a moment

    return sorted(
        p.relative_to(path).as_posix()
        for p in path.rglob("*")
        if p.is_file() and "node_modules" not in p.parts
    ) or [path.as_posix()]


def _run(cmd: list[str], cwd: Path, timeout: int = 900) -> subprocess.CompletedProcess:
    # shell=True on Windows so npm/npx resolve through their .cmd shims;
    # the command list is ours, never agent-supplied, so there is no
    # injection surface here.
    #
    # list2cmdline, NOT " ".join: joining on spaces silently destroys any
    # argument that contains one. `-c user.name=ADZ Factory` became two
    # arguments and every commit failed with "'Factory' is not a git
    # command" — while the gate kept reporting PASS, because committing
    # was never what it checked.
    # encoding/errors explicitly: text=True decodes with the locale
    # codec, which on Windows is cp1252. vitest prints U+2713 check
    # marks, cp1252 has no mapping for those bytes, and the decode
    # raised inside subprocess's reader THREAD — so the exception was
    # printed but not propagated, and .stdout/.stderr came back None.
    # The symptom was a TypeError concatenating None, three frames away
    # from the real cause.
    #
    # Popen + an explicit tree kill, NOT subprocess.run(timeout=...). On
    # a timeout, run() kills the direct child — here cmd.exe, because of
    # shell=True — and then waits for the pipes to close with NO timeout.
    # docker.exe, a grandchild, still held them: a wedged WSL left a
    # `docker ps` hanging a build for as long as anyone let it, its
    # 60-second timeout having fired and done nothing.
    proc = subprocess.Popen(
        subprocess.list2cmdline(cmd) if os.name == "nt" else cmd,
        cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        shell=(os.name == "nt"), start_new_session=(os.name != "nt"),
        # Decoded as utf-8 above, so ask Python children to ENCODE as
        # utf-8 too. Piped, they default to cp1252 on Windows, and every
        # em dash in reachability.py's advice reached the agent as U+FFFD.
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            # Something outside the tree still holds the pipes. Give up
            # on the output rather than on the build.
            out, err = "", ""
        raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err) from None
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _run_infra(cmd: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    """_run for docker and the supabase CLI: a timeout comes back as a
    failed result instead of an exception, so every caller's existing
    returncode check handles a wedged daemon. The "TIMEOUT after Ns"
    wording matches INFRA_ERROR_PATTERNS, so the repair loop classifies
    it as the environment's fault and blames no ticket."""
    try:
        return _run(cmd, cwd, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            cmd, 124, exc.output or "",
            f"TIMEOUT after {timeout}s: `{' '.join(cmd)}` did not answer — "
            f"is Docker/WSL responding?\n{exc.stderr or ''}")


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill a process and everything it started. Same approach as
    runtime_proof.py: taskkill /T on Windows, the process group elsewhere."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, check=False, timeout=30)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    if proc.poll() is None:
        proc.kill()


class TargetRepo:
    """A greenfield repo the factory builds into."""

    def __init__(self, name: str, root: Path | None = None):
        self.name = name
        self.path = (root or WORKSPACE_ROOT) / name

    # ---- scaffolding -------------------------------------------------
    def exists(self) -> bool:
        return (self.path / "package.json").exists()

    @property
    def lock_path(self) -> Path:
        return self.path.parent / f".{self.name}.build.lock"

    def acquire_lock(self) -> tuple[bool, str]:
        """Refuse to build a workspace another run is already writing to.

        Stopping a background build kills the shell, not necessarily the
        Python child. One that was still mid-ticket wrote its files into
        a freshly scaffolded directory seconds after the wipe, and the
        scaffold commit swept them in — the baseline gate then failed on
        code no ticket in that run had produced.

        A stale lock (the process is gone) is reclaimed rather than
        blocking forever, since a killed build never gets to release it.
        """
        import os as _os

        if self.lock_path.exists():
            try:
                pid = int(self.lock_path.read_text(encoding="utf-8").strip() or 0)
            except (ValueError, OSError):
                pid = 0
            if pid and _pid_alive(pid) and pid != _os.getpid():
                return False, (
                    f"another build (pid {pid}) is using {self.path.name}. "
                    f"Stop it, or delete {self.lock_path.name} if it is gone."
                )
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text(str(_os.getpid()), encoding="utf-8", newline="\n")
        return True, "lock acquired"

    def release_lock(self) -> None:
        try:
            self.lock_path.unlink(missing_ok=True)
        except OSError:
            pass

    def scaffold(self, force: bool = False, install: bool = True,
                 extra_packages: list[str] | None = None) -> GateResult:
        """Create the repo, commit the skeleton, install dependencies once.

        `extra_packages` are the architecture's own imports. They are
        merged into package.json BEFORE the single `npm install`, rather
        than added by a second install afterwards: a second resolution
        pass rewrites the tree and drops optional native bindings —
        vitest's rolldown binary vanished that way, and the test step then
        failed with "Cannot find module './rolldown-binding.wasi.cjs'"
        on a repo whose code was fine.
        """
        if self.path.exists() and force:
            survivors = force_wipe(self.path)
            if survivors:
                # `ignore_errors=True` used to hide this: an interrupted
                # run leaves node/tsc holding files open, rmtree skips
                # them, and the "fresh" scaffold starts on a dirty tree.
                # The last time it happened, source files from a killed
                # build failed the baseline gate before any ticket ran.
                return GateResult(
                    False,
                    "--fresh could not empty the target repo. These survived:\n  "
                    + "\n  ".join(survivors[:12])
                    + "\n\nSomething still holds them open — a dev server, a "
                      "`next build`, or an editor. Stop it and re-run.",
                    "force wipe",
                )
        self.path.mkdir(parents=True, exist_ok=True)

        # Deep-copy so the module-level template is never mutated across
        # runs, and merge the architecture's imports in before the single
        # install resolves the whole tree at once.
        package = json.loads(json.dumps(PACKAGE_JSON))
        for name in extra_packages or []:
            package["dependencies"].setdefault(name, "latest")
        (self.path / "package.json").write_text(
            json.dumps(package, indent=2), encoding="utf-8", newline="\n")
        (self.path / "tsconfig.json").write_text(
            json.dumps(TSCONFIG, indent=2), encoding="utf-8", newline="\n")
        # The per-ticket gate runs `tsc -p tsconfig.check.json`, so this
        # file is not optional — without it every gate fails with TS5058
        # before a single ticket is built.
        (self.path / "tsconfig.check.json").write_text(
            json.dumps(TSCONFIG_CHECK, indent=2), encoding="utf-8", newline="\n")
        (self.path / "vitest.config.ts").write_text(VITEST_CONFIG, encoding="utf-8", newline="\n")
        (self.path / ".gitignore").write_text(GITIGNORE, encoding="utf-8", newline="\n")
        (self.path / "README.md").write_text(
            f"# {self.name}\n\nScaffolded by the ADZ factory from an orch2 technical architecture.\n",
            encoding="utf-8", newline="\n")

        for d in SKELETON_DIRS:
            target = self.path / d
            target.mkdir(parents=True, exist_ok=True)
            # git does not track empty directories; .gitkeep makes the
            # intended tree visible in the very first commit.
            (target / ".gitkeep").write_text("", encoding="utf-8")

        for rel, content in SEED_FILES.items():
            path = self.path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")

        self.ensure_git()

        transcript = ["== scaffold =="]
        if install:
            transcript.append("== npm install ==")
            proc = _run(["npm", "install", "--no-audit", "--no-fund"], self.path, timeout=900)
            output = ((proc.stdout or "") + (proc.stderr or "")).strip()
            transcript.append(output[-3000:])
            if proc.returncode != 0:
                # A peer conflict is a THIRD failure class: not code — no
                # agent has written anything yet — and not infrastructure,
                # since the registry is fine. The architecture asked for a
                # package that has not caught up to the toolchain, and
                # retrying changes nothing.
                #
                # A scoped `overrides` block resolves it without
                # downgrading the pin the owner keeps current for security,
                # and without --legacy-peer-deps switching peer checking
                # off for the whole tree. Whether the forced combination
                # actually WORKS is a different question, and the runtime
                # proof is what answers it.
                resolved = self._apply_overrides(output)
                if resolved:
                    transcript.append(f"== peer conflict resolved via overrides: {resolved} ==")
                    proc = _run(["npm", "install", "--no-audit", "--no-fund"],
                                self.path, timeout=900)
                    transcript.append(((proc.stdout or "") + (proc.stderr or "")).strip()[-3000:])
                if proc.returncode != 0:
                    from dependencies import explain_eresolve
                    why = explain_eresolve(output)
                    if why:
                        transcript.append(f"DIAGNOSIS: {why}")
                    return GateResult(False, "\n".join(transcript), "npm install")

        self.commit("chore: scaffold project skeleton")
        return GateResult(True, "\n".join(transcript))

    def _apply_overrides(self, install_output: str) -> str | None:
        """Merge a scoped npm `overrides` block into package.json.

        Returns a description of what was added, or None when nothing
        applied — including when the same block is already present,
        which is what stops a failing install from looping.
        """
        from dependencies import overrides_for_conflict
        block = overrides_for_conflict(install_output)
        if not block:
            return None
        manifest = self.path / "package.json"
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        existing = data.setdefault("overrides", {})
        if all(existing.get(k) == v for k, v in block.items()):
            return None
        existing.update(block)
        manifest.write_text(json.dumps(data, indent=2) + "\n",
                            encoding="utf-8", newline="\n")
        return ", ".join(block)

    def install_packages(self, names: list[str], timeout: int = 900) -> GateResult:
        """Install packages the architecture imports but the scaffold lacks.

        Tickets cannot do this themselves — write_files refuses
        package.json — so a missing dependency is unfixable by the agent
        and burns every retry on `Cannot find module`. The names are
        verified against the registry before they get here.

        Type packages are handled too: `strict` typechecking fails on an
        untyped dependency, and for libraries that ship no declarations
        the fix is @types/<name>, which is a toolchain concern and so
        equally out of a ticket's reach.
        """
        if not names:
            return GateResult(True, "no additional packages required")

        transcript = [f"== npm install {' '.join(names)} =="]
        proc = _run(["npm", "install", "--no-audit", "--no-fund", *names],
                    self.path, timeout=timeout)
        transcript.append(((proc.stdout or "") + (proc.stderr or "")).strip()[-2000:])
        if proc.returncode != 0:
            return GateResult(False, "\n".join(transcript), "npm install")

        untyped = [n for n in names if not self._ships_types(n)]
        type_pkgs = [f"@types/{n.replace('@', '').replace('/', '__')}" for n in untyped]
        if type_pkgs:
            # Best effort: most of these do not exist, and that is fine —
            # a package without @types is not an error, just untyped.
            transcript.append(f"== trying {len(type_pkgs)} @types package(s) ==")
            for tp in type_pkgs:
                _run(["npm", "install", "--no-audit", "--no-fund", "--save-dev", tp],
                     self.path, timeout=180)
        return GateResult(True, "\n".join(transcript))

    def _ships_types(self, name: str) -> bool:
        pkg = self.path / "node_modules" / Path(name) / "package.json"
        if not pkg.exists():
            return True  # not installed; nothing useful to say
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return True
        if data.get("types") or data.get("typings"):
            return True
        return any((pkg.parent).glob("**/*.d.ts"))

    # ---- git ---------------------------------------------------------
    def _git_toplevel(self) -> Path | None:
        proc = _run(["git", "rev-parse", "--show-toplevel"], self.path)
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        return Path(proc.stdout.strip()).resolve()

    def _assert_own_repo(self) -> None:
        """Hard boundary before any git WRITE.

        If the target's .git is missing or broken, git silently walks up
        the tree and operates on the nearest real repo — which here is
        the factory's own source. That actually happened: a partially
        deleted .git left only objects/, the scaffold's `.git exists`
        check skipped re-initialising, and `git add -A` staged 99 files
        in the ADZ repo. An existence check is not an validity check, and
        a write that can escape its directory needs a guard, not care.
        """
        top = self._git_toplevel()
        if top != self.path.resolve():
            raise RuntimeError(
                f"refusing git write in {self.path}: it resolves to repo {top}. "
                f"The target repo is not initialised — run scaffold() first."
            )

    def ensure_git(self) -> None:
        """Initialise git, and verify it took. Re-inits a broken .git
        rather than trusting that the directory's presence means a repo."""
        if self._git_toplevel() == self.path.resolve():
            return
        shutil.rmtree(self.path / ".git", ignore_errors=True)
        _run(["git", "init", "-q", "--initial-branch=main"], self.path)
        if self._git_toplevel() != self.path.resolve():
            raise RuntimeError(f"git init failed in {self.path}")

    def commit(self, message: str) -> bool:
        self._assert_own_repo()
        _run(["git", "add", "-A"], self.path)
        proc = _run(["git", "-c", "user.name=ADZ Factory",
                     "-c", "user.email=factory@adz.local",
                     "commit", "-q", "-m", message], self.path)
        return proc.returncode == 0

    def branch(self, name: str) -> None:
        self._assert_own_repo()
        _run(["git", "checkout", "-q", "-B", name], self.path)

    def log(self, limit: int = 20) -> str:
        if self._git_toplevel() != self.path.resolve():
            return "(target repo not initialised)"
        return _run(["git", "log", "--oneline", f"-{limit}"], self.path).stdout.strip()

    # ---- writing changesets -----------------------------------------
    def write_files(self, files: dict[str, str],
                    allowed_roots: tuple[str, ...] | None = None,
                    allow_new_tests: bool = True) -> list[str]:
        """Write a changeset, enforcing the boundary BEFORE touching disk.

        Three separate refusals:
          - escaping the repo entirely (../../etc);
          - writing outside the roots a ticket owns;
          - inventing a NEW test file during a repair (`allow_new_tests`).

        THE THIRD ONE, AND WHY IT IS NOT "NO NEW FILES"
        -----------------------------------------------
        Told to fix 14 unreachable modules, a repair wrote
        tests/reachability.test.ts — sixteen side-effect imports and
        `expect(true).toBe(true)` — and the check went green while the
        app still returned 404. The builder could not edit package.json
        or tsconfig, but nothing stopped it from authoring a new test
        whose only job was to satisfy a checker.

        A blanket ban on new files would have blocked the REAL fix too:
        what that failure actually needed was a new product file,
        src/app/page.tsx. So the line is drawn between product code and
        tests. During a repair a ticket may create product files and may
        rewrite tests it already owns; it may not conjure a new test.
        A test is evidence, and evidence minted to order is worthless.

        The second used to be checked only after the fact, by
        validate_changeset, which meant an out-of-bounds file was already
        on disk and merely reported. That matters because the toolchain
        config is exactly what must not move: package.json and tsconfig
        define what the gate MEANS, so a changeset that edits them can
        change the verdict rather than satisfy it.
        """
        roots = allowed_roots or ("src/", "tests/", "supabase/")
        repo_root = self.path.resolve()

        planned: list[tuple[Path, str, str]] = []
        for rel, content in files.items():
            normalised = rel.replace("\\", "/").lstrip("./")
            target = (self.path / normalised).resolve()
            if not str(target).startswith(str(repo_root)):
                raise ValueError(f"path escapes the repo: {rel}")
            if not normalised.startswith(roots):
                raise ValueError(
                    f"refusing to write outside {roots}: {rel} "
                    f"(toolchain config defines what the gate means and is not a ticket's to change)"
                )
            if (not allow_new_tests and normalised.startswith("tests/")
                    and not target.exists()):
                raise ValueError(
                    f"refusing to create a new test file during a repair: {rel} "
                    f"(fix the code the gate rejected; a test authored to satisfy "
                    f"a checker is not evidence)"
                )
            planned.append((target, content, normalised))

        written = []
        for target, content, normalised in planned:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
            written.append(normalised)
        return written

    def read_file(self, rel: str) -> str | None:
        p = self.path / rel
        return p.read_text(encoding="utf-8") if p.exists() else None

    def existing_paths(self, globs: tuple[str, ...] = ("src/**/*.ts", "supabase/**/*.sql", "tests/**/*.ts")) -> list[str]:
        out = []
        for pattern in globs:
            for p in self.path.glob(pattern):
                if p.is_file():
                    out.append(p.relative_to(self.path).as_posix())
        return sorted(out)

    def revert_uncommitted(self) -> None:
        """Roll back to the last good commit. A ticket that never passed
        its gate must not leave half-written files for the next ticket to
        compile against.

        Guarded: `git clean -fdq` in the wrong directory would delete
        untracked files from the factory's own repo."""
        self._assert_own_repo()
        _run(["git", "checkout", "--", "."], self.path)
        _run(["git", "clean", "-fdq"], self.path)

    # ---- supabase local stack ----------------------------------------
    def _ports_in_use(self) -> set[int]:
        """Ports Docker has already published, from Docker itself.

        netstat is not enough — it missed bindings that Docker reported,
        and the first two port ranges chosen from netstat output collided
        with already-running projects."""
        proc = _run_infra(["docker", "ps", "--format", "{{.Ports}}"], self.path, timeout=60)
        # A failed `docker ps` must not read as "nothing is running": the
        # empty set would pick 54321, the block other projects occupy.
        if proc.returncode != 0:
            raise RuntimeError(f"cannot list Docker ports: {(proc.stderr or '').strip()[:300]}")
        return {
            int(m) for m in re.findall(r"0\.0\.0\.0:(\d+)", proc.stdout or "")
        }

    def allocate_supabase_ports(self, span: int = 20) -> int:
        """Find a free 100-port block for this project's stack.

        Developers run several Supabase projects at once — this machine
        had 33 supabase containers up across 54321-54527. Defaulting to
        54321 makes `supabase start` fail with "port is already
        allocated", and the CLI's own suggestion is to stop the other
        project. Never do that: it is someone else's work. Move instead.
        """
        used = self._ports_in_use()
        for base in range(54321, 56000, 100):
            if not any(base <= p < base + span for p in used):
                return base
        raise RuntimeError("no free 100-port block for a local Supabase stack")

    def supabase_init(self) -> GateResult:
        """Create config.toml and move the stack off any occupied ports."""
        config = self.path / "supabase" / "config.toml"
        if not config.exists():
            proc = _run_infra(["supabase", "init", "--force", "--workdir", "."], self.path, timeout=180)
            if not config.exists():
                return GateResult(False, (proc.stdout or "") + (proc.stderr or ""), "supabase init")

        try:
            base = self.allocate_supabase_ports()
        except RuntimeError as exc:
            return GateResult(False, str(exc), "docker ps")
        text = config.read_text(encoding="utf-8")
        # Re-map every 54xxx port into the free block, preserving offsets
        # so api/db/studio keep their usual relative positions.
        remapped = re.sub(
            r"(port\s*=\s*)5[45]\d(\d\d)",
            lambda m: f"{m.group(1)}{base // 100}{m.group(2)}",
            text,
        )
        config.write_text(remapped, encoding="utf-8", newline="\n")
        return GateResult(True, f"supabase configured on the {base}-block")

    def supabase_start(self) -> GateResult:
        proc = _run_infra(["supabase", "start"], self.path, timeout=1200)
        output = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            return GateResult(False, output[-3000:], "supabase start")
        return GateResult(True, "supabase stack running")

    DB_TYPES_PATH = "src/lib/database.types.ts"

    def generate_db_types(self) -> GateResult:
        """Generate src/lib/database.types.ts from the applied migrations.

        WHY THIS IS THE SCAFFOLD'S JOB AND NOT A TICKET'S
        -------------------------------------------------
        Supabase's client is generic over a `Database` type, and without a
        real one every row resolves to `never`. Left to themselves, agents
        hand-write that type — and they write it subtly wrong, because
        postgrest's `GenericTable` requires FOUR keys:

            Row, Insert, Update, Relationships

        A hand-written table declares the first three and omits
        `Relationships`. That one omission means no table satisfies
        `GenericTable`, so no schema satisfies `GenericSchema`, so the
        client's schema parameter falls back and EVERY row becomes
        `never`.

        The compiler never says any of that. It reports
        "Property 'id' does not exist on type 'never'" at each use site —
        a pile of errors in the file that consumes the type, none in the
        file that defines it. One ticket burned five attempts chasing
        those symptoms in a route handler while the defect sat in a file
        it had already finished writing.

        So: don't ask an agent to reproduce a type the database can state
        exactly. `supabase gen types` emits it with the right columns AND
        the right shape, and the ticket imports it. This is the same rule
        as Tailwind and the run scripts — anything a ticket cannot fix
        (write_files refuses package.json; nothing lets it learn a private
        postgrest constraint) belongs to the scaffold.

        Needs the stack up and the migrations applied, so it runs after
        the layer that owns supabase/migrations, not at scaffold time.
        """
        if not (self.path / "supabase" / "config.toml").exists():
            return GateResult(False, "no supabase project", "gen types")

        # `db reset` applies the migrations and THEN restarts containers
        # and health-checks them. That last part fails on its own
        # schedule — a refused socket on the storage API — long after the
        # schema is in place, and it exits non-zero for it. Gating on
        # that exit code threw away a perfectly good schema: the reset
        # "failed", yet `gen types` immediately returned all 7 tables.
        #
        # So the reset is best-effort and the TYPES are the verdict. If
        # the migrations really did not apply, gen types comes back with
        # no tables and that is what fails here.
        reset = _run(DB_RESET, self.path, timeout=900)
        reset_out = (reset.stdout or "") + (reset.stderr or "")

        proc = _run_infra(["supabase", "gen", "types", "typescript", "--local"],
                    self.path, timeout=300)
        types = proc.stdout or ""
        if proc.returncode != 0 or "export type Database" not in types:
            detail = reset_out if reset.returncode != 0 else ""
            return GateResult(False, (detail + types + (proc.stderr or ""))[-3000:],
                              "supabase gen types")

        # A schema with no tables means the migrations did not land,
        # whatever the reset said. Writing that file would be worse than
        # writing none: the builder would import a Database type that
        # knows about nothing.
        if "Relationships: [" not in types:
            return GateResult(False, (reset_out + types)[-3000:],
                              "supabase gen types (no tables)")

        out = self.path / self.DB_TYPES_PATH
        out.parent.mkdir(parents=True, exist_ok=True)
        header = (
            "// GENERATED by `supabase gen types typescript --local`.\n"
            "// Do not edit, and do not hand-write a Database type beside it:\n"
            "// this one carries the Relationships key postgrest requires.\n"
        )
        out.write_text(header + types, encoding="utf-8", newline="\n")
        tables = types.count("Relationships: [")
        return GateResult(True, f"{self.DB_TYPES_PATH}: {tables} table(s)")

    def supabase_stop(self) -> None:
        """Stop only THIS project's stack, by id. Never a bare
        `supabase stop`, which would take down every project running on
        the machine."""
        _run_infra(["supabase", "stop", "--project-id", self.name], self.path, timeout=300)

    # ---- the gate ----------------------------------------------------
    def run_gate(self, commands: list[list[str]], timeout: int = 600,
                 log=None, ticket: str | None = None) -> GateResult:
        """Run the repo's own toolchain. Short-circuits on first failure
        so the agent gets one clear error rather than a pile.

        `log` is an optional RunLog. Every command is timed and emitted,
        which is what makes "why did this ticket take four minutes"
        answerable afterwards rather than only while watching it."""
        transcript = []
        for cmd in commands:
            label = " ".join(cmd)
            transcript.append(f"== {label} ==")
            started = time.time()
            try:
                proc = _run(cmd, self.path, timeout=timeout)
            except subprocess.TimeoutExpired:
                transcript.append(f"TIMEOUT after {timeout}s")
                if log:
                    log.gate(label, False, round(time.time() - started, 2), ticket,
                             f"TIMEOUT after {timeout}s")
                return GateResult(False, "\n".join(transcript), label)
            output = ((proc.stdout or "") + (proc.stderr or "")).strip()
            # tsc is verbose on success and terse on failure; keep the tail,
            # which is where the errors and the summary live.
            transcript.append(output[-6000:] if output else "(no output)")
            if log:
                log.gate(label, proc.returncode == 0, round(time.time() - started, 2),
                         ticket, output)
            if proc.returncode != 0:
                transcript.append(f"\nFAILED: {label} (exit {proc.returncode})")
                return GateResult(False, "\n".join(transcript), label)
        transcript.append("\nAll gate commands passed.")
        return GateResult(True, "\n".join(transcript))


# `npm run` rather than `npx`: npx will happily fall back to a global
# package (or a stub that tells you off) when node_modules is missing,
# which produces a confusing gate failure that has nothing to do with the
# code. npm scripts resolve node_modules/.bin or fail cleanly.
TYPECHECK = ["npm", "run", "--silent", "typecheck"]
TEST = ["npm", "run", "--silent", "test"]
NEXT_BUILD = ["npx", "next", "build"]
ROUTE_TYPEGEN = ["npm", "run", "--silent", "typegen"]
ROUTE_CONTRACTS = ["npm", "run", "--silent", "check:routes"]
# Our own check, run as a subprocess so it reports through the same
# GateResult path as every other step rather than needing a special case.
REACHABILITY = [sys.executable, str(REPO_ROOT / "reachability.py"), "."]
RUNTIME_PROOF = [sys.executable, str(REPO_ROOT / "runtime_proof.py"), "."]
VISUAL_REVIEW = [sys.executable, str(REPO_ROOT / "visual_review.py"), "."]
CLEAN_INSTALL = [sys.executable, str(REPO_ROOT / "clean_install.py"), "."]
DB_RESET = ["supabase", "db", "reset"]

# Per-ticket: seconds, so it can run on every repair attempt.
PER_TICKET_GATE = [TYPECHECK, TEST]

# Integration: minutes. Runs once, over the whole repo, and catches the
# class of defect a typechecker structurally cannot see:
#   next build       webpack module resolution (tsc's "bundler" mode
#                    resolves `./x.js` to x.ts; webpack does not), and
#                    framework contracts like "a route file may only
#                    export HTTP handlers".
#   supabase db reset  whether the generated SQL actually APPLIES.
#                    Checking that a migration file exists and is
#                    non-trivial says nothing about whether Postgres
#                    accepts it.
INTEGRATION_GATE = [TYPECHECK, TEST]


EDGE_FUNCTIONS_DIR = "supabase/functions"


def edge_function_files(repo: "TargetRepo") -> list[str]:
    root = repo.path / EDGE_FUNCTIONS_DIR
    if not root.exists():
        return []
    return sorted(p.relative_to(repo.path).as_posix() for p in root.rglob("*.ts"))


def deno_check_cmd(files: list[str]) -> list[str]:
    return ["deno", "check", *files]


def _is_committed_repo(repo: "TargetRepo") -> tuple[bool, str]:
    if not (repo.path / ".git").exists():
        return False, "not a git repo"
    if not (repo.path / "package-lock.json").exists():
        return False, "no package-lock.json to install from"
    return True, "committed repo with a lockfile"


def _has_proof_screenshot(repo: "TargetRepo") -> tuple[bool, str]:
    """A visual review needs a screenshot and a key.

    Both are genuinely optional — no browser or no API key costs the
    review, not the build — so say SKIPPED with the reason rather than
    folding an unmeasured step into a pass.
    """
    if not (repo.path / "artifacts" / "proof-of-work.png").exists():
        return False, "no proof-of-work screenshot (runtime proof captured none)"
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        return False, "no API key for the vision model"
    return True, "screenshot present"


def _has_production_build(repo: "TargetRepo") -> tuple[bool, str]:
    """A runtime proof needs something built to serve.

    Runs after `next build` in the step order, so in a healthy run this
    is satisfied. Saying so explicitly means a missing build is reported
    SKIPPED with a reason rather than counted as a pass.
    """
    if not (repo.path / "package.json").exists():
        return False, "no package.json"
    if not (repo.path / ".next" / "BUILD_ID").exists():
        return False, "no production build in .next (next build did not run)"
    return True, "production build present"


def _has_entry_points(repo: "TargetRepo") -> tuple[bool, str]:
    from reachability import entry_points
    entries = entry_points(repo.path)
    if not entries:
        return False, "no framework entry points to walk from"
    return True, f"{len(entries)} entry point(s)"


def _deno_available(repo: "TargetRepo") -> tuple[bool, str]:
    if not _have("deno"):
        return False, "deno not on PATH"
    if not edge_function_files(repo):
        return False, "no supabase/functions/*.ts in this project"
    return True, f"{len(edge_function_files(repo))} edge function file(s)"


def assert_tests_ran(output: str) -> str | None:
    """Fail a green test run that ran no tests.

    `--passWithNoTests` is in the scaffold on purpose: the BASELINE gate
    runs before any ticket has written a test, and a red baseline would
    make the first ticket inherit a failure it did not cause. The flag is
    right there and wrong here.

    By integration time every ticket has landed, so a suite that ran zero
    tests is not a pass — it is a gate that stopped measuring. This repo
    reached that state: a repair deleted the last test file because it
    imported a module being removed, and vitest stayed green over an
    empty directory for the rest of the run.

    Reads the count out of the runner's own summary rather than trusting
    the exit code, which only ever meant "nothing failed".
    """
    # vitest: "Tests  5 passed (5)"  |  "No test files found"
    counted = re.search(r"^\s*Tests\s+.*?\((\d+)\)", output, re.MULTILINE)
    if counted:
        return None if int(counted.group(1)) > 0 else "the suite ran 0 tests"
    if re.search(r"No test (files )?found", output, re.IGNORECASE):
        return ("the suite ran 0 tests — --passWithNoTests turned an empty "
                "tests/ directory into a green gate")
    # Some runners print only a file summary. Accept a positive one.
    files = re.search(r"^\s*Test Files\s+.*?\((\d+)\)", output, re.MULTILINE)
    if files and int(files.group(1)) == 0:
        return "no test files ran"
    return None


@dataclass
class Step:
    """An integration step plus how to tell whether it can run at all.

    A step that cannot run is reported SKIPPED with its reason and never
    folded into a pass. A gate that silently skips is worse than no gate:
    it produces the word "PASS" while measuring nothing.
    """
    name: str
    cmd: "list[str] | Callable[[TargetRepo], list[str]]"
    precheck: "Callable[[TargetRepo], tuple[bool, str]]"
    timeout: int = 900
    # Retries are for steps whose failures are sometimes the environment
    # rather than the code. Only ever set it where that is actually true;
    # retrying a real gate failure just hides it.
    retries: int = 0
    # An exit code says "nothing failed". It does not say "something was
    # checked". `vitest --passWithNoTests` exits 0 over an empty
    # directory, and this repo shipped exactly that: an agent deleted the
    # last test file and the suite stayed green while verifying nothing.
    #
    # Given the step's output, return an error string to fail it despite
    # exit 0, or None to accept. This is the difference between reading
    # a return code and reading the log.
    assert_output: "Callable[[str], str | None] | None" = None

    def resolve(self, repo: "TargetRepo") -> list[str]:
        """Static commands pass through; callables are given the repo.

        `deno check` takes explicit paths rather than a glob — the shell
        that runs it will not expand one on Windows — so the edge-function
        step has to look at the tree before it can name its command."""
        return self.cmd(repo) if callable(self.cmd) else self.cmd


def _have(binary: str) -> bool:
    return shutil.which(binary) is not None


def _next_available(repo: "TargetRepo") -> tuple[bool, str]:
    if not (repo.path / "node_modules" / ".bin").exists():
        return False, "node_modules missing — run scaffold with install=True"
    return True, "next present"


def _supabase_available(repo: "TargetRepo") -> tuple[bool, str]:
    if not _have("supabase"):
        return False, "supabase CLI not on PATH"
    if not _have("docker"):
        return False, "docker not on PATH"
    proc = _run_infra(["docker", "info"], repo.path, timeout=60)
    if proc.returncode != 0:
        return False, "docker daemon not responding"
    if not (repo.path / "supabase" / "config.toml").exists():
        return False, "supabase/config.toml missing — run supabase_init()"
    return True, "supabase CLI + docker ready"


INTEGRATION_STEPS = [
    Step("typecheck", TYPECHECK, lambda r: (True, "always"), timeout=600),
    Step("tests", TEST, lambda r: (True, "always"), timeout=600,
         assert_output=assert_tests_ran),
    Step("next build", NEXT_BUILD, _next_available, timeout=900),
    Step("route typegen", ROUTE_TYPEGEN, _next_available, timeout=600),
    # Supabase Edge Functions are Deno, not Node: they live outside
    # tsconfig's `include` and `tsc` never sees them. Without this step a
    # ticket whose whole deliverable is an edge function is gated only on
    # "the file exists and isn't tiny".
    Step("edge functions (deno)", lambda r: deno_check_cmd(edge_function_files(r)),
         _deno_available, timeout=600),
    Step("route contracts", ROUTE_CONTRACTS, _next_available, timeout=600),
    # Asks the one question no compiler asks: is each file the factory
    # built actually reachable from an entry point. pm-mcp-server passed
    # every other gate and shipped a server with zero tools registered.
    Step("reachability", REACHABILITY, _has_entry_points, timeout=120),
    # The local stack restarts its containers at the end of a reset, and
    # the storage container is sometimes not listening yet when the CLI
    # probes it. The migration has already applied by then.
    # Every step above is STATIC: they ask whether the code compiles,
    # typechecks, tests and wires up. None of them ever started the
    # server and made a request — which is how a repo whose `/` returned
    # 404 passed the entire gate, twice. "Does it compile" and "does it
    # run" are different questions.
    Step("runtime proof", RUNTIME_PROOF, _has_production_build, timeout=300),
    # The only step whose checker is a model, and the only one that can
    # see what none of the others can: Tailwind never installed, text
    # white on white, a button covering the heading. A class name that
    # resolves to nothing is valid TypeScript, so this factory once
    # shipped an unstyled app through a fully green gate.
    #
    # It fails ONLY on a CRITICAL finding, and its own "PASS" is
    # discarded and recomputed from its findings — a model's opinion can
    # fail a build here, never rescue one another step already failed.
    Step("visual review", VISUAL_REVIEW, _has_proof_screenshot, timeout=300),
    # Everything above runs against the workspace the factory built —
    # the one environment where node_modules is already correct. This
    # clones the committed HEAD somewhere clean and installs from the
    # lockfile alone, which is the only way to see a repo that works
    # for us and for nobody else. It found exactly that on its first
    # run: a lockfile out of sync with its own package.json.
    Step("clean install", CLEAN_INSTALL, _is_committed_repo, timeout=1800),
    Step("supabase db reset", DB_RESET, _supabase_available, timeout=900, retries=2),
]


# A gate failure means "the code is wrong". These say "the machine was
# busy" — a container still booting, a port in use, a socket refused.
# Treating them as code failures made a transient 502 blame four tickets
# and queue repairs against code that was already correct, which is worse
# than not checking at all: it spends tokens to make good code different.
INFRA_ERROR_PATTERNS = (
    r"dial tcp", r"connection refused", r"actively refused",
    r"failed to execute http request", r"Error status 5\d\d",
    r"port is already allocated", r"failed to set up container networking",
    r"TIMEOUT after \d+s", r"ECONNREFUSED", r"EADDRINUSE",
    r"upstream server", r"docker daemon",
    # Seen during "Initialising schema..." — the CLI's own container
    # setup, before any migration runs, so it cannot be the SQL.
    r"error running container", r"Conflict\. The container name",
    r"already in use by container",
)


def is_infra_failure(text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in INFRA_ERROR_PATTERNS)


@dataclass
class IntegrationResult:
    passed: bool
    ran: list[str]
    skipped: list[tuple[str, str]]
    failed: str | None
    transcript: str
    infra: bool = False

    def summary(self) -> str:
        parts = [f"{len(self.ran)} ran"]
        if self.infra:
            parts.append("INFRASTRUCTURE failure (not attributable to code)")
        if self.skipped:
            parts.append(f"{len(self.skipped)} SKIPPED (" +
                         "; ".join(f"{n}: {why}" for n, why in self.skipped) + ")")
        if self.failed:
            parts.append(f"FAILED at {self.failed}")
        return ", ".join(parts)


def run_integration(repo: "TargetRepo", steps: list[Step] | None = None,
                    start_supabase: bool = True, log=None) -> IntegrationResult:
    """Run the integration steps, reporting skips as skips.

    `passed` means every step that COULD run did run and succeeded. It
    never means "nothing ran, so nothing failed" — callers get the
    skip list and can decide whether a skipped step was load-bearing.
    """
    steps = steps or INTEGRATION_STEPS
    ran: list[str] = []
    skipped: list[tuple[str, str]] = []
    transcript: list[str] = []

    for step in steps:
        ok, reason = step.precheck(repo)
        if not ok:
            skipped.append((step.name, reason))
            transcript.append(f"== {step.name} == SKIPPED: {reason}")
            if log:
                log.integration_step(step.name, "skip", 0.0, reason)
            continue

        # The database stack has to be up before a reset can apply
        # anything to it.
        if step.name == "supabase db reset" and start_supabase:
            started = repo.supabase_start()
            transcript.append(f"== supabase start ==\n{started.transcript[-600:]}")
            if not started.passed:
                skipped.append((step.name, "supabase start failed"))
                if log:
                    log.integration_step(step.name, "skip", 0.0, "supabase start failed")
                continue

        transcript.append(f"== {step.name} ==")
        step_started = time.time()
        result = repo.run_gate([step.resolve(repo)], timeout=step.timeout)
        # Retry ONLY on an infrastructure failure. A real gate failure is
        # reported the first time: retrying that would just take longer to
        # deliver the same verdict, and could mask a genuine flake in the
        # code itself.
        for attempt in range(step.retries):
            if result.passed or not is_infra_failure(result.transcript):
                break
            transcript.append(f"-- infra failure, retry {attempt + 1}/{step.retries} --")
            time.sleep(5)
            result = repo.run_gate([step.resolve(repo)], timeout=step.timeout)
        # Exit code 0 is necessary, not sufficient.
        if result.passed and step.assert_output:
            complaint = step.assert_output(result.transcript)
            if complaint:
                result = GateResult(False,
                                    result.transcript + f"\n\nGATE VACUOUS: {complaint}",
                                    step.name)
        elapsed = round(time.time() - step_started, 2)
        transcript.append(result.transcript[-6000:])
        ran.append(step.name)
        if log:
            log.integration_step(step.name, "pass" if result.passed else "fail",
                                 elapsed, "", "" if result.passed else result.transcript)
        if not result.passed:
            return IntegrationResult(False, ran, skipped, step.name,
                                     "\n".join(transcript),
                                     infra=is_infra_failure(result.transcript))

    return IntegrationResult(True, ran, skipped, None, "\n".join(transcript))


if __name__ == "__main__":
    import sys

    repo = TargetRepo(sys.argv[1] if len(sys.argv) > 1 else "pm-mcp-server")
    print(f"Scaffolding {repo.path} ...")
    result = repo.scaffold(force="--force" in sys.argv, install="--no-install" not in sys.argv)
    print(result.transcript[-1500:])
    print("\nscaffold:", "OK" if result.passed else f"FAILED at {result.failed_command}")
    if result.passed:
        gate = repo.run_gate(PER_TICKET_GATE)
        print("empty-repo gate:", "PASS" if gate.passed else f"FAIL ({gate.failed_command})")
        print(gate.transcript[-800:])
