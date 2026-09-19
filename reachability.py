"""
reachability.py — is every file the factory built actually wired in?

THE GAP THIS CLOSES
-------------------
The pm-mcp-server build passed every gate and shipped a server that
registered zero tools. Seven tool modules and five prompt modules
registered themselves as an import side effect (`mcp.addTool(...)` at
module scope), and nothing ever imported them — so none of the twelve
registrations ran.

Every gate was right to pass. `tsc` typechecks each file in isolation,
vitest runs the tests that exist, `next build` compiles the routes it can
find, and `deno check` validates the edge functions. None of them asks
*"is this module reachable from an entry point"*, because for a library
that question has no answer. For a factory's output it does: a file that
nothing imports is work that was paid for and cannot run.

This is the blind spot in first-writer-wins ownership. The DAG guarantees
a ticket's dependencies EXIST before it builds; nothing guarantees a
ticket's output is CONSUMED afterwards. `mcp_init` owned src/index.ts, so
the seven later tickets that added tools could not edit it, and no ticket
was responsible for wiring them in.

HOW IT WORKS
------------
Walk the import graph from the framework's real entry points and report
any owned .ts/.tsx file the walk never reaches. Deliberately syntactic —
a regex over import specifiers, not a type-aware resolver. It needs to
answer one coarse question and stay dependency-free; the cost is that an
exotic import form could be missed, which fails safe (a file is called
reachable when in doubt, never unreachable).
"""

import re
from pathlib import Path

# Framework entry points. These are reachable by definition: a Next.js
# route file is loaded by the router, an edge function by the Supabase
# runtime, a test by vitest. Nothing in the repo imports them.
ENTRY_GLOBS = (
    "src/index.ts",
    "src/app/**/page.tsx", "src/app/**/page.ts",
    "src/app/**/route.ts", "src/app/**/route.tsx",
    "src/app/**/layout.tsx", "src/app/**/layout.ts",
    "src/app/**/template.tsx", "src/app/**/loading.tsx",
    "src/app/**/error.tsx", "src/app/**/not-found.tsx",
    "supabase/functions/*/index.ts",
    # Standalone scripts and cron jobs are invoked directly, not imported.
    # This is the conventional home the contract fallback writes them to.
    "src/scripts/**/*.ts",
    "tests/**/*.test.ts", "tests/**/*.test.tsx",
    # Config files are loaded by their own tool, never imported by the
    # app. vitest.config.ts showed up as an orphan until it was listed.
    "*.config.ts", "*.config.mts", "*.config.tsx",
)

SOURCE_SUFFIXES = (".ts", ".tsx")

# `from "x"`, `import "x"`, `import("x")`, `export ... from "x"`,
# `require("x")` — one pattern, because the specifier is what matters and
# the surrounding syntax is not.
_IMPORT_RE = re.compile(r"""(?:from|import|require)\s*\(?\s*["']([^"']+)["']""")


def parse_specifiers(text: str) -> list[str]:
    return _IMPORT_RE.findall(text)


def resolve(spec: str, importer: Path, repo: Path) -> Path | None:
    """Resolve one specifier to a file in the repo, or None for packages.

    Handles the extensionless style this factory enforces, the `.js`
    suffix TypeScript lets you write for a `.ts` file, the `@/` alias
    from tsconfig paths, and directory/index resolution.
    """
    if spec.startswith("@/"):
        base = repo / "src" / spec[2:]
    elif spec.startswith("."):
        base = (importer.parent / spec).resolve()
    else:
        return None  # bare package, jsr:, npm:, node: — not our file

    candidates = [base]
    # "./x.js" in TypeScript source almost always means ./x.ts
    if base.suffix in (".js", ".jsx"):
        candidates.append(base.with_suffix(".ts"))
        candidates.append(base.with_suffix(".tsx"))
    for suffix in SOURCE_SUFFIXES:
        candidates.append(base.with_name(base.name + suffix))
        candidates.append(base / f"index{suffix}")

    for candidate in candidates:
        if candidate.is_file() and candidate.suffix in SOURCE_SUFFIXES:
            return candidate
    return None


def entry_points(repo: Path) -> list[Path]:
    found: list[Path] = []
    for pattern in ENTRY_GLOBS:
        found.extend(p for p in repo.glob(pattern) if p.is_file())
    return sorted(set(found))


def reachable_files(repo: Path) -> set[Path]:
    """Every source file reachable from an entry point, transitively."""
    seen: set[Path] = set()
    queue = list(entry_points(repo))
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        try:
            text = current.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for spec in parse_specifiers(text):
            target = resolve(spec, current, repo)
            if target and target not in seen:
                queue.append(target)
    return seen


def check_reachability(repo_path: Path, owned: dict[str, str] | None = None
                       ) -> tuple[bool, str, list[str]]:
    """(passed, message, unreachable_paths).

    `owned` maps path -> owning ticket, so the message can name the
    ticket whose work is stranded AND the entry points that should have
    imported it. Both are needed: the file's owner knows what it is for,
    and the entry point's owner is the one that has to import it.
    """
    repo = repo_path.resolve()
    reachable = reachable_files(repo)
    entries = entry_points(repo)

    if not entries:
        # No entry points means the question is unanswerable, not that
        # everything failed. Say so rather than failing the build.
        return True, "no entry points found — reachability not checked", []

    candidates = [
        p for p in repo.rglob("*")
        if p.is_file() and p.suffix in SOURCE_SUFFIXES
        and "node_modules" not in p.parts and ".next" not in p.parts
        and not p.name.endswith(".d.ts")
    ]

    orphans = sorted(
        p.relative_to(repo).as_posix() for p in candidates if p not in reachable
    )
    if owned is not None:
        # Only hold the factory responsible for files it built.
        orphans = [p for p in orphans if p in owned]

    if not orphans:
        return True, f"{len(reachable)} file(s) reachable from {len(entries)} entry point(s)", []

    summary = (
        "They typecheck, but nothing can run them — any side effect they"
        " perform at module scope (registering a tool, a route, a handler)"
        " never happens."
    )
    lines = [
        f"{len(orphans)} file(s) are never imported from any entry point.",
        summary,
        "",
    ]
    for path in orphans:
        owner = f"  (ticket: {owned[path]})" if owned and path in owned else ""
        lines.append(f"  UNREACHABLE  {path}{owner}")
    lines += ["", "Entry points searched:"]
    lines += [f"  {p.relative_to(repo).as_posix()}" for p in entries[:12]]
    advice = (
        "Fix by importing them from the entry point that should own them."
        " If they register themselves on a shared object imported from that"
        " entry point, use dynamic import() inside the startup function — a"
        " static import would run them before the object is initialised."
    )
    lines += ["", advice]
    return False, "\n".join(lines), orphans


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    ok, message, orphans = check_reachability(target)
    print(message)
    raise SystemExit(0 if ok else 1)
