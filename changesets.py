"""
changesets.py — contracts and builders for multi-file code changes

A software factory's unit of work is a CHANGESET: several files landing
together in a repo, judged by that repo's toolchain. This module holds
the two pieces that differ from the single-artifact factory:

  ChangesetContract   which paths a ticket owns, and what must be true of
                      them, plus the repo gate commands that must pass.
  ChangesetAgent      produces many files in one turn and repairs them
                      against compiler output.

WHERE THE CONTRACT COMES FROM
-----------------------------
Not from a model. orch2's worker output already names the files it is
specifying — the documents are literally structured as
"### 2. Core TypeScript Interfaces (`src/types/index.ts`)" followed by
the code. So ownership is extracted deterministically by pairing each
path mention with the code fence that follows it.

That matters beyond saving a pro-tier call: a model asked to invent the
file list would invent a DIFFERENT list than the architecture specifies,
and the whole point is to build the architecture that was designed, not
a plausible neighbour of it.

FIRST WRITER WINS
-----------------
Tickets reference each other's files constantly (planning_engine imports
types from mcp_init). Ownership is assigned in dependency order, so a
path belongs to the first ticket that provides code for it and later
tickets are told to import it, not rewrite it. Without this, a late
ticket silently clobbers an earlier one's types and the failure surfaces
as an unrelated compiler error three tickets later.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from google import genai

# Roots a changeset may write to. Anything outside is rejected before it
# reaches the repo — see TargetRepo.write_files for the second boundary.
ALLOWED_ROOTS = ("src/", "tests/", "supabase/")

# output_format hints -> the extension the deliverable should carry.
FORMAT_EXTENSIONS = {
    "sql": (".sql",),
    "schema": (".sql", ".ts"),
    "typescript": (".ts", ".tsx"),
    "next.js": (".ts", ".tsx"),
    "component": (".ts", ".tsx"),
    "code": (".ts", ".tsx", ".sql"),
}

# Alternation order matters: `ts` before `tsx` matches the first three
# characters of "page.tsx" and stops, silently yielding "page.ts" — a
# path that does not exist. Longest extension first.
_PATH_RE = re.compile(r"((?:src|tests|supabase)/[A-Za-z0-9_\-./\[\]]+\.(?:tsx|ts|sql))")

# Many documents put the path in a header comment on the first line of
# the code itself (`// src/types/dashboard.ts`) rather than in the prose
# above it. That is the strongest ownership signal available: the file
# is naming itself.
_SELF_NAMING_RE = re.compile(
    r"\A\s*(?://|--|/\*|#)\s*((?:src|tests|supabase)/[A-Za-z0-9_\-./\[\]]+\.(?:tsx|ts|sql))"
)
_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_+-]*\n(.*?)^```", re.MULTILINE | re.DOTALL)

MIN_FENCE_CHARS = 120

# When a document presents code but never names a file, fall back to a
# conventional path derived from its output_format. db_schema is exactly
# this case: 6KB of SQL under the heading "SQL Schema Implementation",
# with no filename anywhere in the document.
FORMAT_FALLBACK_PATHS = [
    (("sql", "schema", "migration"), "supabase/migrations/{order:04d}_{ticket}.sql"),
    (("component", "next.js", "route", "frontend"), "src/app/{ticket}.tsx"),
    (("typescript", "code", "logic", "tool", "algorithm"), "src/{ticket}.ts"),
]


class ChangesetError(Exception):
    """A ticket cannot be turned into a buildable changeset."""


@dataclass
class ChangesetContract:
    ticket_id: str
    required_paths: list[str]
    gate: list[list[str]] = field(default_factory=list)
    min_bytes: int = 120

    def describe(self) -> str:
        return f"{len(self.required_paths)} file(s): {', '.join(self.required_paths[:4])}" + (
            f" +{len(self.required_paths) - 4} more" if len(self.required_paths) > 4 else ""
        )

    def to_dict(self) -> dict:
        return {
            "ticket_id": self.ticket_id,
            "required_paths": self.required_paths,
            "min_bytes": self.min_bytes,
        }


def extract_owned_paths(architecture: str) -> list[str]:
    """Paths this document actually provides code for, in document order.

    The window for each fence is its whole SECTION — everything since the
    previous fence ended — rather than a fixed number of characters. A
    fixed window looked right on the mcp_init document, where the path
    sits in the heading immediately above the code, and silently found
    nothing on nextjs_frontend, where a paragraph of explanation comes
    between the heading and the fence. Section boundaries are the real
    structure; character counts were a proxy that happened to fit one
    document.
    """
    owned: list[str] = []
    cursor = 0
    for fence in _FENCE_RE.finditer(architecture):
        section = architecture[cursor:fence.start()]
        cursor = fence.end()
        body = fence.group(1)
        if len(body.strip()) < MIN_FENCE_CHARS:
            continue  # a snippet, not a file

        # A path comment at the top of the code beats anything in the
        # prose: the file is declaring its own location.
        self_named = _SELF_NAMING_RE.match(body)
        if self_named:
            path = self_named.group(1)
        else:
            matches = _PATH_RE.findall(section)
            if not matches:
                continue
            path = matches[-1]  # the mention closest to the code wins

        if path not in owned:
            owned.append(path)
    return owned


def fallback_path(ticket: dict) -> str | None:
    """A conventional path for a document that presents code but names no
    file. Deterministic, and derived from the planner's own
    output_format rather than guessed by a model."""
    fmt = (ticket.get("output_format") or "").lower()
    for keywords, template in FORMAT_FALLBACK_PATHS:
        if any(k in fmt for k in keywords):
            return template.format(order=ticket.get("priority", 1), ticket=ticket["id"])
    return None


def _extension_ok(path: str, output_format: str) -> bool:
    fmt = output_format.lower()
    allowed: set[str] = set()
    for key, exts in FORMAT_EXTENSIONS.items():
        if key in fmt:
            allowed.update(exts)
    if not allowed:
        return True  # no usable hint; don't invent a constraint
    return Path(path).suffix in allowed


def contract_for_ticket(ticket: dict, gate: list[list[str]],
                        already_owned: set[str]) -> ChangesetContract:
    """Derive a changeset contract, deterministically. Raises
    ChangesetError when the ticket specifies no files of its own."""
    candidates = extract_owned_paths(ticket["architecture"])

    kept, rejected = [], []
    for path in candidates:
        if not path.startswith(ALLOWED_ROOTS):
            rejected.append(f"{path} (outside {ALLOWED_ROOTS})")
            continue
        if path in already_owned:
            continue  # an earlier ticket in the DAG owns it
        if not _extension_ok(path, ticket.get("output_format", "")):
            rejected.append(f"{path} (extension vs output_format '{ticket.get('output_format')}')")
            continue
        kept.append(path)

    if not kept:
        guess = fallback_path(ticket)
        if guess and guess not in already_owned:
            kept = [guess]
        else:
            detail = f"; rejected: {', '.join(rejected)}" if rejected else ""
            raise ChangesetError(
                f"no ownable files found in the architecture for '{ticket['id']}'{detail}"
            )

    return ChangesetContract(ticket_id=ticket["id"], required_paths=kept, gate=gate)


def validate_changeset(contract: ChangesetContract, repo, written: list[str]) -> tuple[bool, str]:
    """Deterministic pre-gate: did the agent actually deliver the files it
    was contracted for, with real content in them?

    Runs BEFORE the toolchain because `tsc` is happy to pass a repo where
    a required file simply doesn't exist and nothing imports it. Passing
    the compiler is necessary, not sufficient.
    """
    missing = [p for p in contract.required_paths if repo.read_file(p) is None]
    if missing:
        return False, f"required file(s) not created: {', '.join(missing)}"

    thin = []
    for p in contract.required_paths:
        content = repo.read_file(p) or ""
        if len(content.strip()) < contract.min_bytes:
            thin.append(f"{p} ({len(content.strip())} bytes)")
    if thin:
        return False, f"file(s) below {contract.min_bytes} bytes: {', '.join(thin)}"

    stray = [p for p in written if not p.startswith(ALLOWED_ROOTS)]
    if stray:
        return False, f"wrote outside permitted roots: {', '.join(stray)}"

    return True, f"{len(contract.required_paths)} required file(s) present"


# ---- The builder -----------------------------------------------------
FILE_BEGIN = "=== FILE:"
FILE_END = "=== END FILE ==="

SYSTEM_INSTRUCTION = """\
You are a senior TypeScript engineer implementing one subtask of an
already-designed system. The architecture has been written for you: do
not redesign it, implement it.

Emit files in exactly this format, and nothing else — no prose before,
between, or after:

=== FILE: src/path/to/file.ts ===
<the complete contents of the file>
=== END FILE ===

Rules:
- Emit every file listed under "Files you must create". Complete contents
  each time, never a diff, an excerpt, or "... rest unchanged".
- Do NOT wrap file contents in markdown code fences.
- The project is strict TypeScript, ESM, target ES2022, `strict: true`.
  Code must compile under `tsc --noEmit` with no errors and no `any`
  used to silence one.
- Write relative imports WITHOUT a file extension ("./lib/supabase",
  not "./lib/supabase.js"). The project uses moduleResolution
  "bundler"; a .js suffix resolves under tsc but breaks the
  Turbopack build that Next 16 uses by default.
- Import existing files by their real relative path. Files listed under
  "Already in the repo" exist — import from them, never redefine their
  exports.
- No placeholder bodies, no TODO, no `throw new Error('not implemented')`.
- External calls (Supabase, network) must be structured so the logic is
  testable without a live service.
- NEVER throw at module scope for missing configuration, and never read
  required env vars at module scope. Importing a module must always
  succeed. Validate configuration lazily inside the function or factory
  that needs it (e.g. a `getSupabaseClient()` that throws on first use),
  because the test suite imports every module and no environment is
  configured when it does.
- Keep pure logic (scheduling, scoring, optimisation) in functions that
  take plain data and return plain data, with no client or I/O inside.
  That is what makes it testable, and it is where the real behaviour is.
- When you add a test, put it in tests/ and name it *.test.ts. Tests run
  under vitest with globals enabled.
"""


def parse_files(text: str) -> dict[str, str]:
    """Parse the delimiter format. Chosen over JSON because TypeScript is
    full of quotes, backslashes and newlines, and a single escaping slip
    invalidates an entire JSON response — whereas a malformed delimiter
    block costs only that one file."""
    files: dict[str, str] = {}
    pattern = re.compile(
        rf"^{re.escape(FILE_BEGIN)}\s*(.+?)\s*===\s*$\n(.*?)^{re.escape(FILE_END)}\s*$",
        re.MULTILINE | re.DOTALL,
    )
    for match in pattern.finditer(text):
        path = match.group(1).strip().strip("`").lstrip("./")
        body = match.group(2)
        # Strip a fence the model added despite instructions, but only
        # when it wraps the whole file.
        fence = re.match(r"\A```[a-zA-Z0-9_+-]*\n(.*)\n```\s*\Z", body.strip(), re.DOTALL)
        files[path] = (fence.group(1) if fence else body).rstrip() + "\n"
    return files


class ChangesetAgent:
    """One build session for one ticket. Session continuity matters: the
    repair turn must remember the files it just wrote, so a compiler
    error is enough context to fix them."""

    def __init__(self, model: str, system_instruction: str | None = None):
        self.client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.model = model
        interaction = self.client.interactions.create(
            model=model, input=system_instruction or SYSTEM_INSTRUCTION,
        )
        self._last = interaction.id

    def _call(self, text: str) -> dict[str, str]:
        interaction = self.client.interactions.create(
            model=self.model, input=text, previous_interaction_id=self._last,
        )
        self._last = interaction.id
        return parse_files(interaction.output_text)

    def write(self, ticket: dict, contract: ChangesetContract,
              repo_tree: list[str], dependency_context: str) -> dict[str, str]:
        required = "\n".join(f"  {p}" for p in contract.required_paths)
        existing = "\n".join(f"  {p}" for p in repo_tree) or "  (none yet)"
        return self._call(
            f"# Subtask: {ticket['title']}\n\n"
            f"## Intent\n{ticket.get('intent', '')}\n\n"
            f"## Expected output\n{ticket.get('output_format', '')}\n\n"
            f"## Architecture to implement\n{ticket['architecture']}\n\n"
            f"## Already in the repo (import from these; do not redefine)\n{existing}\n\n"
            f"{dependency_context}"
            f"## Files you must create\n{required}\n\n"
            f"Emit those files now, in the delimiter format."
        )

    def repair(self, failure: str, contract: ChangesetContract) -> dict[str, str]:
        required = ", ".join(contract.required_paths)
        return self._call(
            f"The repo's toolchain REJECTED your changeset:\n\n```\n{failure[-5000:]}\n```\n\n"
            f"Fix the cause. Re-emit every file you need to change, complete, in the "
            f"delimiter format. Files under contract: {required}. "
            f"Do not weaken types or add `any` to silence an error — fix the real problem."
        )
