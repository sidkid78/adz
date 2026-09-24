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


def types_package(name: str) -> str:
    """The DefinitelyTyped name for a package: @scope/n -> @types/scope__n."""
    if name.startswith("@"):
        scope, _, rest = name[1:].partition("/")
        return f"@types/{scope}__{rest}"
    return f"@types/{name}"


def types_for(names: list[str], timeout: int = 60) -> list[str]:
    """@types/* companions for packages that ship no declarations.

    A package without bundled types is a dependency failure the AGENT
    CANNOT FIX. `write_files` refuses package.json on purpose, so a
    ticket importing canvas-confetti gets TS7016 ("could not find a
    declaration file") and has no way to resolve it — every retry burns
    on a problem that is not in any file it owns.

    Two questions, both answered by the registry rather than guessed:
    does the package declare its own types, and does DefinitelyTyped
    publish some? Only when the first is no and the second is yes do we
    add one, because installing @types beside a package that already
    bundles them is how you get duplicate-identifier errors.
    """
    wanted: list[str] = []
    for name in names:
        own = subprocess.run(
            ["npm", "view", name, "types", "typings"],
            capture_output=True, text=True, timeout=timeout,
            check=False, shell=(sys.platform == "win32"),
        )
        if (own.stdout or "").strip():
            continue                      # ships its own declarations

        candidate = types_package(name)
        found = subprocess.run(
            ["npm", "view", candidate, "version"],
            capture_output=True, text=True, timeout=timeout,
            check=False, shell=(sys.platform == "win32"),
        )
        if found.returncode == 0:
            wanted.append(candidate)
    return wanted


def explain_eresolve(output: str) -> str | None:
    """Turn an ERESOLVE wall into the one sentence that matters.

    npm reports a peer conflict in about twenty-five lines of tree, and
    the factory printed all of them. The useful content is three facts:
    what is installed, what wanted something else, and what range it
    wanted.

    This is a THIRD failure class, distinct from the two the factory
    already knows. It is not a code failure — no agent wrote anything
    yet. It is not an infrastructure failure — the network and the
    registry are fine. The architecture asked for packages that cannot
    coexist with the toolchain, and no number of retries changes that.
    """
    if "ERESOLVE" not in output:
        return None
    found = re.search(r"Found:\s*(\S+)", output)
    # The conflict is the peer line AFTER "Could not resolve dependency:".
    # Matching the first peer line in the whole log finds a constraint
    # that is satisfied and names the wrong package — here it blamed
    # drei@^19, which was fine, instead of fiber's ">=19 <19.3".
    tail = output.split("Could not resolve dependency:", 1)
    scope = tail[1] if len(tail) > 1 else output
    peer = re.search(r"peer\s+(\S+?)@\"([^\"]+)\"\s+from\s+(\S+)", scope)
    if not (found and peer):
        return "npm could not resolve a dependency tree (ERESOLVE)"
    package, wanted, requester = peer.group(1), peer.group(2), peer.group(3)
    return (f"dependency conflict: {requester} requires {package}@{wanted}, "
            f"but the scaffold installs {found.group(1)}. "
            f"The architecture's packages have not caught up to the toolchain "
            f"version this factory pins.")


def overrides_for_conflict(output: str) -> dict | None:
    """A scoped npm `overrides` block that resolves an ERESOLVE, or None.

    Three ways out of a peer conflict, and only one is honest:

      --legacy-peer-deps   turns peer checking off for the WHOLE tree, so
                           the next genuine conflict is silent too.
      downgrade the pin    the owner keeps React and Next current on
                           purpose, for security. Walking that back to
                           satisfy a view library trades a CVE for a
                           convenience.
      overrides            tells npm that THIS package may use the
                           version the root project already installs.
                           Scoped to one dependency, visible in
                           package.json, and reversible when upstream
                           catches up.

    `$react` is npm's own syntax for "whatever the root project
    resolved", so the override cannot drift away from the real pin.

    This resolves the INSTALL. It does not promise the combination
    works — a peer range usually reflects a real incompatibility, and
    forcing it can produce a tree that installs and then white-screens.
    That is what the runtime proof and the console-error check are for.
    """
    if "ERESOLVE" not in output:
        return None
    tail = output.split("Could not resolve dependency:", 1)
    scope = tail[1] if len(tail) > 1 else output
    peer = re.search(r"peer\s+(\S+?)@\"[^\"]+\"\s+from\s+(\S+?)@\S+", scope)
    if not peer:
        return None
    wanted, requester = peer.group(1), peer.group(2)
    # react-dom moves with react; overriding one without the other just
    # relocates the same conflict.
    pinned = {wanted: f"${wanted}"}
    if wanted == "react":
        pinned["react-dom"] = "$react-dom"
    return {requester: pinned}


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
