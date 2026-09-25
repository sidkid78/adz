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

An import has to plausibly DO something to count — a referenced
binding, or a bare/discarded-dynamic import of a module with
module-scope effects. `import * as X; void X` is found, not used, and
does not count. See live_specifiers().
"""

import json
import re
from pathlib import Path

# PRODUCT entry points: the ones a user or a runtime actually reaches.
# A Next.js route file is loaded by the router, an edge function by the
# Supabase runtime, a script by whoever invokes it. Nothing in the repo
# imports them, and reaching code from here means the code can RUN.
PRODUCT_ENTRY_GLOBS = (
    "src/app/**/page.tsx", "src/app/**/page.ts",
    "src/app/**/route.ts", "src/app/**/route.tsx",
    "src/app/**/layout.tsx", "src/app/**/layout.ts",
    "src/app/**/template.tsx", "src/app/**/loading.tsx",
    "src/app/**/error.tsx", "src/app/**/not-found.tsx",
    "src/app/**/global-error.tsx", "src/app/**/default.tsx",
    # Next convention files the framework loads by NAME, at the project
    # or src root. Omitting middleware.ts made it a false orphan: its
    # only legitimate owner is the framework, so no import of it can
    # ever exist and no agent could have satisfied the check.
    "middleware.ts", "src/middleware.ts",
    "instrumentation.ts", "src/instrumentation.ts",
    "supabase/functions/*/index.ts",
    # Standalone scripts and cron jobs are invoked directly, not imported.
    # This is the conventional home the contract fallback writes them to.
    "src/scripts/**/*.ts",
)

# HARNESS roots: real files, but reaching code from HERE proves nothing
# about the product.
#
# These used to sit in one list with the product entries, and an agent
# under repair pressure found the hole immediately. Told to fix 14
# unreachable modules, it wrote tests/reachability.test.ts — sixteen
# side-effect imports and `expect(true).toBe(true)` — plus a barrel that
# re-exported all of them. Reachability went green. The app still had no
# page.tsx and `/` still returned 404: not one of those modules had
# become reachable by any user.
#
# A test importing a module proves the module parses. It is the same
# vacuity `validate_contract()` rejects for contracts, and reachability
# needed its own guard. So these stay roots for THEMSELVES — a test file
# is not an orphan — but they confer reachability on nothing.
HARNESS_ENTRY_GLOBS = (
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


# ---- Imports that exist only to be found --------------------------------
# The test-file cheat moved into a page. Told that four modules were
# unreachable, a repair added this to src/app/(dashboard)/page.tsx:
#
#     import * as _guardrails from "../../lib/agents/guardrails";
#     void _guardrails;
#
# The page never called a guardrail. Reachability went green, and it cost
# 286k tokens — more than the whole first build — to make four modules
# LOOK consumed. Meanwhile the trend API route still never called the
# trend agent: the real wiring gap was covered, not closed.
#
# So an import confers reachability only when it plausibly does work:
#   - a binding import counts if some binding is referenced other than as
#     `void X` — a call, a JSX tag, a type position, a re-export;
#   - a bare `import "./x"` counts only if x does something at module
#     scope (the self-registering tool pattern this module exists for);
#     importing a module of plain declarations for its side effects runs
#     nothing.
# Both tests are generous: any mention counts as a use, any top-level call
# counts as an effect. When unsure, the edge counts — this check must
# never strand real code, only refuse imports that visibly do nothing.

# `import [type] <clause> from "spec"`. Quotes are excluded from the
# clause so a bare import on the line above can't be swallowed into it.
_BINDING_IMPORT_RE = re.compile(
    r"""^[ \t]*import\s+(?:type\s+)?(?P<clause>[^;"']*?)\s+from\s+["'](?P<spec>[^"']+)["'][ \t]*;?""",
    re.MULTILINE | re.DOTALL)
_BARE_IMPORT_RE = re.compile(r"""^[ \t]*import\s+["'](?P<spec>[^"']+)["'][ \t]*;?""", re.MULTILINE)
# `await import("./x");` as a statement of its own — the result thrown
# away. The advice text recommends dynamic import() for self-registering
# modules, and a repair applied it to modules that register nothing, in
# instrumentation.ts. Same test as a bare import: only effects count.
_DISCARDED_DYNAMIC_RE = re.compile(
    r"""^[ \t]*(?:await\s+|void\s+)?import\(\s*["'](?P<spec>[^"']+)["']\s*\)[ \t]*;?[ \t]*$""",
    re.MULTILINE)
_IDENT = r"[A-Za-z_$][\w$]*"

# Top-level lines that declare rather than do. Anything else at column 0
# (`mcp.addTool(`, `register(`, `await init()`) is an effect.
_DECLARATION_START = re.compile(
    r"^(?:export\s+(?:default\s+)?)?(?:declare\s+)?(?:async\s+)?"
    r"(?:function|class|abstract|interface|type|enum|const|let|var|namespace)\b"
    r"|^(?:import|export\s*[{*]|//|/\*|\*|[}\])]|$)")


def _bindings(clause: str) -> list[str]:
    """Local names an import clause introduces: `X`, `* as X`,
    `{ a, b as c, type d }`, or a default plus either of the others."""
    names: list[str] = []
    braces = re.search(r"\{([^}]*)\}", clause)
    if braces:
        for item in braces.group(1).split(","):
            item = re.sub(r"^\s*type\s+", "", item).strip()
            if item:
                names.append(item.split(" as ")[-1].strip())
        clause = clause[:braces.start()] + clause[braces.end():]
    for part in clause.split(","):
        part = part.strip()
        if part.startswith("*"):
            names.append(part.split(" as ")[-1].strip())
        elif part:
            names.append(part)
    return [n for n in names if re.fullmatch(_IDENT, n)]


def _is_used(name: str, body: str) -> bool:
    """Referenced anywhere except as the operand of `void`."""
    mention = re.compile(rf"(?<![\w$]){re.escape(name)}(?![\w$])")
    return any(not re.search(r"\bvoid\s*\(?\s*$", body[:m.start()][-12:])
               for m in mention.finditer(body))


def has_module_effects(text: str) -> bool:
    """Does loading this module DO anything? Syntactic and generous: a
    top-level statement that is not a declaration counts, and so does a
    top-level declaration whose initialiser calls something
    (`export const tool = server.tool(...)`).

    Template strings and block comments are blanked first: a system
    prompt in a backtick string has lines at column 0, and
    "CRITICAL INTEGRITY CONSTRAINTS:" once read as a module-scope
    statement."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"`(?:\\.|[^`\\])*`", "``", text, flags=re.DOTALL)
    for line in text.splitlines():
        if not line or line[0] in " \t":
            continue
        if not _DECLARATION_START.match(line):
            return True
        if re.match(r"^(?:export\s+)?(?:const|let|var)\b[^=]*=.*\(", line):
            return True
        if re.match(r"^export\s+default\s+(?!function|class|async)", line) and "(" in line:
            return True
    return False


def live_specifiers(text: str, importer: Path, repo: Path) -> list[str]:
    """Specifiers whose import plausibly does work (see above). Everything
    parse_specifiers finds, minus binding imports nothing uses and bare
    imports of modules with no module-scope effects."""
    dead: set[tuple[int, int]] = set()
    body = _BINDING_IMPORT_RE.sub(lambda m: " " * len(m.group(0)), text)
    for m in _BINDING_IMPORT_RE.finditer(text):
        names = _bindings(m.group("clause"))
        if names and not any(_is_used(n, body) for n in names):
            dead.add(m.span())
    for m in [*_BARE_IMPORT_RE.finditer(text), *_DISCARDED_DYNAMIC_RE.finditer(text)]:
        target = resolve(m.group("spec"), importer, repo)
        if target is None:
            continue
        try:
            effects = has_module_effects(target.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            effects = True
        if not effects:
            dead.add(m.span())
    live = []
    for m in _IMPORT_RE.finditer(text):
        if not any(start <= m.start() < end for start, end in dead):
            live.append(m.group(1))
    return live


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


def _manifest_loads_index(repo: Path) -> bool:
    """Does package.json actually load src/index.ts?

    pm-mcp-server's src/index.ts IS the product: `npm run mcp` executes
    it and it registers every tool. The microlearn build's src/index.ts
    was a barrel — `export * from` every stranded module — written for
    no reason but to make them look reachable, and nothing anywhere
    loaded it.

    The two are indistinguishable by shape, so ask the manifest instead
    of guessing: main/module/bin/exports, or any script that names it.
    That is the difference between an entry point and a file that merely
    hopes to be one.
    """
    manifest = repo / "package.json"
    if not manifest.exists():
        return False
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    fields = [data.get("main"), data.get("module"), data.get("types")]
    fields += list(data.get("scripts", {}).values())
    bin_field = data.get("bin")
    fields += list(bin_field.values()) if isinstance(bin_field, dict) else [bin_field]
    fields.append(json.dumps(data.get("exports", "")))
    return any("src/index" in str(f) for f in fields if f)


def entry_points(repo: Path, harness: bool = True) -> list[Path]:
    """Entry points. `harness=False` gives only the PRODUCT ones —
    the set that decides whether shipped code can actually run."""
    globs = PRODUCT_ENTRY_GLOBS + (HARNESS_ENTRY_GLOBS if harness else ())
    found: list[Path] = []
    for pattern in globs:
        found.extend(p for p in repo.glob(pattern) if p.is_file())
    index = repo / "src" / "index.ts"
    if index.is_file() and _manifest_loads_index(repo):
        found.append(index)
    return sorted(set(found))


def reachable_files(repo: Path, harness: bool = True) -> set[Path]:
    """Every source file reachable from an entry point, transitively."""
    seen: set[Path] = set()
    queue = list(entry_points(repo, harness=harness))
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        try:
            text = current.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for spec in live_specifiers(text, current, repo):
            target = resolve(spec, current, repo)
            if target and target not in seen:
                queue.append(target)
    return seen


def untouched_scaffold_files(repo: Path) -> set[str]:
    """Files still exactly as the scaffold committed them.

    The scaffold ships src/index.ts (a placeholder only its smoke test
    imports) into every project. In a Next app nothing should import it,
    so it was an orphan in EVERY build — a failure no ticket could fix
    honestly, and the one the reachability cheats kept trying to satisfy
    (`import '../index'` in the root layout, `void _index` in a page).

    The factory answers for what tickets built. A scaffold file nobody
    has changed is not that; once a ticket modifies one, it is, and it
    is checked like any other."""
    import subprocess

    def git(*args: str) -> str:
        try:
            proc = subprocess.run(["git", *args], cwd=repo, capture_output=True,
                                  text=True, encoding="utf-8", timeout=60, check=False)
        except (OSError, subprocess.SubprocessError):
            return ""
        return proc.stdout if proc.returncode == 0 else ""

    roots = git("rev-list", "--max-parents=0", "HEAD").split()
    if not roots:
        return set()
    seeded = set(git("ls-tree", "-r", "--name-only", roots[-1]).splitlines())
    changed = set(git("diff", "--name-only", roots[-1]).splitlines())
    return seeded - changed


def check_reachability(repo_path: Path, owned: dict[str, str] | None = None
                       ) -> tuple[bool, str, list[str]]:
    """(passed, message, unreachable_paths).

    `owned` maps path -> owning ticket, so the message can name the
    ticket whose work is stranded AND the entry points that should have
    imported it. Both are needed: the file's owner knows what it is for,
    and the entry point's owner is the one that has to import it.
    """
    repo = repo_path.resolve()
    # PRODUCT entries only. Reaching a module from a test says it parses,
    # not that anything runs it.
    reachable = reachable_files(repo, harness=False)
    entries = entry_points(repo, harness=False)

    if not entries:
        # No entry points means the question is unanswerable, not that
        # everything failed. Say so rather than failing the build.
        return True, "no entry points found — reachability not checked", []

    # A Next app whose only routes are API handlers has no user-facing
    # surface at all. `/` returns 404, and every component built for it
    # is stranded by definition. This shipped twice before anything
    # noticed, because no compiler asks whether a product has a page.
    app_dir = repo / "src" / "app"
    if app_dir.is_dir() and not list(app_dir.rglob("page.tsx")) \
            and not list(app_dir.rglob("page.ts")):
        return False, (
            "The app has no page route: src/app contains no page.tsx.\n"
            "Every component built for the UI is unreachable by definition "
            "and `/` returns 404.\n\n"
            "Fix by adding src/app/page.tsx that renders the top-level "
            "component(s) this app exists to show."
        ), ["src/app/page.tsx"]

    candidates = [
        p for p in repo.rglob("*")
        if p.is_file() and p.suffix in SOURCE_SUFFIXES
        and "node_modules" not in p.parts and ".next" not in p.parts
        and not p.name.endswith(".d.ts")
        # Harness files answer to their own tool, not to the product.
        and "tests" not in p.parts and ".config" not in p.name
    ]

    orphans = sorted(
        p.relative_to(repo).as_posix() for p in candidates if p not in reachable
    )
    if owned is not None:
        # Only hold the factory responsible for files it built.
        orphans = [p for p in orphans if p in owned]
    scaffold = untouched_scaffold_files(repo)
    orphans = [p for p in orphans if p not in scaffold]

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
        " static import would run them before the object is initialised.\n\n"
        "A test that imports them does NOT count, and neither does a barrel"
        " that re-exports them: only the entry points listed above are"
        " searched. Neither does an import nothing uses — `import * as X`"
        " followed by `void X`, or a bare `import \"./x\"` of a module that"
        " only declares things. The entry point has to CALL the module. If"
        " a module has no entry point that should own it, the missing thing"
        " is the entry point — write that."
    )
    lines += ["", advice]
    return False, "\n".join(lines), orphans


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    ok, message, orphans = check_reachability(target)
    print(message)
    raise SystemExit(0 if ok else 1)
