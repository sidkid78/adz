"""
api_surface.py — show the builder the API that is actually installed

THE PROBLEM
-----------
An architecture specified Inngest code in the v3 style:

    new Inngest<Events>({ ... })
    inngest.createFunction(config, trigger, handler)     // 3 arguments

`npm install inngest` installs 4.20.0, where the generic is ClientOptions
rather than an events map and createFunction takes two arguments. The
ticket failed typechecking twice and would have failed five times, because
the builder kept writing the API it remembered and the architecture kept
confirming it.

This is the same failure as the deprecated `gemini-1.5-flash` /
`@google/generative-ai` the orchestrator was specifying: an architecture
is written from training data, and training data ages. Patching that one
library upstream fixed one library. It does not scale — there is no list
of every npm package a future architecture might name.

THE FIX
-------
Stop relying on anyone's memory. The installed package ships `.d.ts`
files that state its real signatures, so hand the builder an excerpt of
those for the packages its ticket actually imports. The compiler was
always going to arbitrate; this just lets the agent read the same source
before writing rather than after failing.

Deliberately a summary, not the whole declaration file. `@types/node` is
megabytes; a ticket needs the shape of the entry points it calls, and an
unbounded dump would crowd out the architecture it is meant to implement.
"""

import json
import re
from pathlib import Path

# Top-level declarations worth showing: what you can import and call.
_DECL_RE = re.compile(
    r"^\s*(?:export\s+)?declare\s+(?:const|function|class|abstract\s+class|type|interface|enum)\s+\w[^\n]*"
    r"|^\s*export\s+(?:default\s+)?(?:function|class|abstract\s+class|const|type|interface|enum)\s+\w[^\n]*",
    re.MULTILINE,
)

MAX_CHARS_PER_PACKAGE = 2600
MAX_DECLS_PER_PACKAGE = 40
# Declaration files bigger than this are ambient platform types
# (@types/node, dom.d.ts); their bulk is never what a ticket needs.
MAX_DTS_BYTES = 400_000


def _entry_declaration_files(pkg_dir: Path) -> list[Path]:
    """The .d.ts files a package points at, preferring its declared entry.

    Walking every .d.ts in a package gives internal helpers equal weight
    with the public surface, so the declared `types` entry comes first and
    a shallow scan only fills in behind it.
    """
    files: list[Path] = []
    manifest = pkg_dir / "package.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        declared = data.get("types") or data.get("typings")
        if declared:
            candidate = pkg_dir / declared
            if candidate.suffix != ".d.ts":
                candidate = candidate.with_suffix(".d.ts")
            if candidate.is_file():
                files.append(candidate)

    # Then the files most likely to hold the package's own surface: an
    # index, or a file named after the package itself. Inngest keeps its
    # class in components/Inngest.d.ts, and a flat glob of the root found
    # only framework adapters — lots of `serve` overloads, none of the
    # `Inngest` class whose generic was failing to typecheck.
    # Path.stem strips only the LAST suffix, so "Inngest.d.ts" stems to
    # "Inngest.d" and never matched the package name. Every ranking below
    # silently fell through to "other", which is why a flat scan returned
    # koa and nuxt adapters instead of the Inngest class that was failing.
    def base(p: Path) -> str:
        return p.name.removesuffix(".d.ts").lower()

    package_stem = pkg_dir.name.lower()
    ranked = sorted(
        (p for p in pkg_dir.rglob("*.d.ts") if p.is_file()),
        key=lambda p: (
            0 if base(p) == package_stem else
            1 if base(p) in ("index", "types", "client", "main") else 2,
            len(p.relative_to(pkg_dir).parts),
            -p.stat().st_size,   # among equals, the fuller file first
        ),
    )
    for path in ranked:
        if path not in files:
            files.append(path)
        if len(files) >= 8:
            break
    return files


def summarize_package(repo_path: Path, name: str) -> str:
    """A bounded excerpt of a package's real declarations, or ""."""
    pkg_dir = repo_path / "node_modules" / Path(name)
    if not pkg_dir.is_dir():
        return ""

    version = ""
    manifest = pkg_dir / "package.json"
    if manifest.exists():
        try:
            version = json.loads(manifest.read_text(encoding="utf-8")).get("version", "")
        except (json.JSONDecodeError, OSError):
            version = ""

    decls: list[str] = []
    for dts in _entry_declaration_files(pkg_dir):
        try:
            if dts.stat().st_size > MAX_DTS_BYTES:
                continue
            text = dts.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in _DECL_RE.finditer(text):
            line = match.group(0).strip().rstrip("{").strip()
            if line and line not in decls:
                decls.append(line)
            if len(decls) >= MAX_DECLS_PER_PACKAGE:
                break
        if len(decls) >= MAX_DECLS_PER_PACKAGE:
            break

    if not decls:
        return ""

    header = f"### {name}{f' @ {version}' if version else ''} (installed)"
    body, used = [], 0
    for line in decls:
        if used + len(line) > MAX_CHARS_PER_PACKAGE:
            body.append("  … (truncated)")
            break
        body.append(f"  {line}")
        used += len(line)
    return header + "\n" + "\n".join(body)


def api_surface_for(repo_path: Path, packages: list[str], budget: int = 9000) -> str:
    """Declarations for the packages a ticket imports, within a budget.

    Returned as a prompt block. Empty when nothing useful was found, so a
    ticket that imports only local files pays nothing.
    """
    blocks, used = [], 0
    for name in packages:
        block = summarize_package(repo_path, name)
        if not block:
            continue
        if used + len(block) > budget:
            break
        blocks.append(block)
        used += len(block)

    if not blocks:
        return ""
    return (
        "\n## Installed API surface — THIS is what the packages actually export\n"
        "The architecture below may have been written against an older "
        "version of these libraries. Where they disagree, THESE signatures "
        "win: they were read from the installed package and the compiler "
        "will enforce them.\n\n" + "\n\n".join(blocks) + "\n"
    )


if __name__ == "__main__":
    import sys

    repo = Path(sys.argv[1])
    names = sys.argv[2:]
    out = api_surface_for(repo, names)
    print(out or "(nothing found)")
