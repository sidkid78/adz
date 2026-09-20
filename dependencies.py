"""
dependencies.py — install what the architecture actually imports

THE GAP THIS CLOSES
-------------------
A ticket failed its gate twice with:

    Cannot find module 'inngest'
    Cannot find module '@google/genai'
    Cannot find module '@remotion/lambda'

None were in the scaffold's package.json, and the build agent could not
add them: write_files refuses package.json by design, because a changeset
that edits the toolchain can change what the gate MEANS rather than
satisfy it. So the ticket was unfixable — it would have burned every
retry and failed for a reason no amount of rewriting could address.

The scaffold cannot carry a fixed list either. Which packages a project
needs is a property of the architecture, not of the factory: this one
wanted Inngest and Remotion, the previous one wanted FastMCP.

So the packages are extracted from the architecture, the same way file
paths are — deterministically, from what the document actually writes.

WHY EXTRACTION ALONE IS NOT ENOUGH
----------------------------------
A naive scan of import specifiers pulls in a lot that is not a package:
`@/components` is a tsconfig path alias, `topics` and `profiles` come
from SQL `FROM "topics"`, and base64 blobs inside string literals parse
as specifiers too. Installing that list fails outright, and one bad name
takes the whole `npm install` with it.

Two filters handle it. Only TypeScript/JavaScript fences are scanned, so
SQL never contributes. Then every surviving candidate is checked against
the npm registry before anything is installed — which also disposes of
hallucinated package names, since a model that invents a plausible
library cannot invent it into existing.
"""

import re
import subprocess
import sys
from pathlib import Path

# Fences whose contents are JS/TS. An untagged fence is scanned too, but
# only when it looks like module code — SQL is the common untagged case
# and its FROM "table" clauses otherwise read as imports.
_CODE_FENCE_RE = re.compile(
    r"^```([a-zA-Z0-9_+-]*)\n(.*?)^```", re.MULTILINE | re.DOTALL
)
_JS_LANGS = {"ts", "tsx", "typescript", "javascript", "js", "jsx", "mts", "cts"}

_SPECIFIER_RE = re.compile(
    r"""(?:^|\s)(?:import|export)[^'"\n]*?from\s*['"]([^'"]+)['"]"""
    r"""|(?:^|\s)import\s*['"]([^'"]+)['"]"""
    r"""|\brequire\(\s*['"]([^'"]+)['"]\s*\)""",
    re.MULTILINE,
)

# npm's own name rules, which double as a noise filter: a base64 blob or
# a capitalised table name cannot satisfy them.
_NPM_NAME_RE = re.compile(r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$")

# Runtime-provided or locally-aliased; never npm installs.
_SKIP_PREFIXES = ("node:", "jsr:", "npm:", "http:", "https:", "data:", "@/", "~/", ".", "/")
_NODE_BUILTINS = {
    "fs", "path", "os", "crypto", "http", "https", "url", "util", "stream",
    "events", "buffer", "child_process", "worker_threads", "zlib", "net",
    "assert", "process", "timers", "readline", "querystring", "tls", "dns",
}


def package_of(specifier: str) -> str | None:
    """The installable package a specifier belongs to.

    Subpaths collapse to their package: `inngest/next` -> `inngest`, and
    `@remotion/lambda` stays whole because the scope is part of the name.
    """
    if not specifier or specifier.startswith(_SKIP_PREFIXES):
        return None
    parts = specifier.split("/")
    name = "/".join(parts[:2]) if specifier.startswith("@") else parts[0]
    if name in _NODE_BUILTINS or not _NPM_NAME_RE.match(name):
        return None
    return name


def extract_packages(architecture: str) -> set[str]:
    """Bare package imports appearing in the document's JS/TS code."""
    found: set[str] = set()
    for lang, body in _CODE_FENCE_RE.findall(architecture):
        language = lang.lower()
        if language and language not in _JS_LANGS:
            continue  # sql, bash, json, mermaid — not module code
        if not language and "import " not in body and "require(" not in body:
            continue
        for groups in _SPECIFIER_RE.findall(body):
            specifier = next((g for g in groups if g), "")
            name = package_of(specifier)
            if name:
                found.add(name)
    return found


def packages_for_architecture(arch: dict) -> set[str]:
    return {
        pkg
        for ticket in arch.get("tickets", [])
        for pkg in extract_packages(ticket.get("architecture", ""))
    }


def verify_on_npm(names: set[str], timeout: int = 60) -> tuple[list[str], list[str]]:
    """Split candidates into (real, unknown) by asking the registry.

    This is the filter that makes auto-install safe. A model can write a
    confident import for a library that does not exist; the registry is
    the one authority that settles it, and checking here means a bad name
    is reported rather than failing the whole install.
    """
    real, unknown = [], []
    for name in sorted(names):
        proc = subprocess.run(
            ["npm", "view", name, "version"],
            capture_output=True, text=True, timeout=timeout,
            check=False, shell=(sys.platform == "win32"),
        )
        (real if proc.returncode == 0 else unknown).append(name)
    return real, unknown


def missing_from(repo_path: Path, names: list[str]) -> list[str]:
    """Drop anything the scaffold already declares, so a re-run is cheap."""
    import json

    pkg_json = repo_path / "package.json"
    if not pkg_json.exists():
        return names
    data = json.loads(pkg_json.read_text(encoding="utf-8"))
    declared = {*data.get("dependencies", {}), *data.get("devDependencies", {})}
    return [n for n in names if n not in declared]


if __name__ == "__main__":
    import json

    arch = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    candidates = packages_for_architecture(arch)
    print(f"{len(candidates)} candidate(s) from the architecture:")
    print("  " + ", ".join(sorted(candidates)))
    real, unknown = verify_on_npm(candidates)
    print(f"\non npm ({len(real)}): {', '.join(real)}")
    if unknown:
        print(f"NOT on npm ({len(unknown)}): {', '.join(unknown)}")
