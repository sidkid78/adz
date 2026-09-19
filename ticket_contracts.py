"""
ticket_contracts.py — per-ticket acceptance contracts, for any artifact kind

WHAT A CONTRACT IS
------------------
A ticket's contract says three things:
  - what FILE the ticket produces (kind + filename)
  - what deterministic CHECKS that file must pass (see artifact_kinds.py)
  - for Python tickets only, the pytest suite to run

Every check is a plain function over the file. No check asks a model
whether the work is good. That is what keeps the repo's central rule —
"code decides pass/fail, never the agent" — true for deliverables that
aren't code.

WHY IT ISN'T JUST PYTEST
------------------------
The factory originally graded everything against one global add() test
suite, so any ticket could "PASS" by writing add(). The first fix made
tests per-ticket but still assumed every deliverable was a Python
module, which meant the factory refused most real work. A ticket asking
for a webhook field mapping or an escalation runbook is a perfectly
buildable deliverable; it just isn't Python. Kinds fix that: markdown
documents, JSON contracts and YAML configs get gates appropriate to
them (required sections, parseable fences, required keys, no unfilled
placeholders, minimum substance per section).

THE ANTI-SELF-GRADING GUARDS
----------------------------
Unchanged in spirit from the Python-only version, because they are what
make a model-authored gate trustworthy:

  1. SEPARATION — authored by the reasoning tier in its own session,
     BEFORE any build agent exists. The builder never sees this prompt.
  2. FREEZING — written to contracts/<slug>.json and reused verbatim on
     every retry and every parallel racer.
  3. TAMPER-PROOFING — the executor rewrites the test file (and never
     reads checks from the workspace) on every run, so an agent editing
     its own gate accomplishes nothing.
  4. NON-VACUITY — validate_contract() runs the checks against an EMPTY
     artifact and rejects the contract unless they FAIL. A gate that
     passes an empty file measures nothing, and it is exactly what a
     model produces when asked to grade a vague spec.
  5. SPECIFICITY — a contract made only of the kind's mandatory checks
     is rejected. It must assert something drawn from THIS ticket.

REFUSAL
-------
Still allowed, but now rare: only for tickets whose deliverable is an
action in the world (make a phone call, sign a contract, click through
a vendor's UI) rather than a file. Those escalate to a human.
"""

import ast
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from artifact_kinds import CHECKS, KINDS, run_checks

REPO_ROOT = Path(__file__).resolve().parent
CONTRACTS_DIR = REPO_ROOT / "contracts"

# Contract authoring is reasoning work, not volume work — it gets the
# planning tier regardless of what tier the ticket's build route uses.
CONTRACT_AUTHOR_MODEL = "gemini-3.1-pro-preview"

# How many non-mandatory, ticket-derived checks a contract must carry
# before it counts as actually measuring this ticket.
MIN_SPECIFIC_CHECKS = 2


def _kind_catalogue() -> str:
    lines = []
    for name, meta in KINDS.items():
        lines.append(f"  {name} ({meta['extension']}) — {meta['description']}")
        lines.append(f"      checks you may use: {', '.join(meta['allowed'])}")
    return "\n".join(lines)


CONTRACT_AUTHOR_SYSTEM_INSTRUCTION = f"""
You define the acceptance contract for one engineering ticket: the file
it must produce, and the deterministic checks that file must pass.

Respond with a single JSON object and nothing else. No markdown fences.

{{
  "buildable": true | false,
  "reason": "<one sentence; required when buildable is false>",
  "kind": "<one of the kinds below>",
  "artifact_name": "<snake_case name, no extension>",
  "checks": [ {{"type": "<check name>", ...params}} ],
  "test_code": "<complete pytest file source; ONLY for python_module>"
}}

KINDS
{_kind_catalogue()}

CHECK PARAMETERS
  required_sections      sections: [str]   headings that must exist
  min_words_per_section  count: int, level: int (default 2)
  min_words              count: int
  min_table_rows         count: int
  fenced_blocks_parse    (no params)
  json_required_keys     keys: [str]       dotted paths; "a.b", "items[].id"
  yaml_required_keys     keys: [str]
  min_collection_size    path: str, count: int, format: "json"|"yaml"
  regex_present          pattern: str, description: str
  regex_absent           pattern: str, description: str
  ruff / mypy            (no params; python_module only)
  pytest_suite           test_filename: str (python_module only)

RULES
- Choose the kind that matches the DELIVERABLE the ticket describes. Most
  integration, mapping, runbook, policy and architecture tickets are
  markdown_document. Payload contracts, field maps and metric definitions
  are json_document. Pipeline and deployment config is yaml_config. Only
  choose python_module when the ticket genuinely asks for runnable code.
- Your checks must be SPECIFIC TO THIS TICKET. Include at least two
  checks drawn from its actual content: the real section names it calls
  for, the real system or field names it must reference, the real
  minimum number of mapping rows. Do not emit a generic contract.
- Use regex_present to pin down concrete facts the deliverable must
  contain — an API version, an SLA threshold, a named system. Keep each
  pattern loose enough to survive reasonable wording changes.
- Never write a check that an EMPTY file would pass.
- Do not add checks that require network access, credentials, or a live
  system. Everything must be decidable from the file alone.
- Set buildable to false ONLY when the deliverable is an action in the
  world rather than a file (placing calls, signing contracts, clicking
  through a vendor console). Producing the spec, runbook, mapping or
  config that describes such work IS buildable — prefer that.
"""


class ContractError(Exception):
    """Raised when a ticket cannot be given an honest acceptance contract."""


@dataclass
class TicketContract:
    slug: str
    kind: str
    artifact_name: str
    checks: list[dict] = field(default_factory=list)
    test_code: str | None = None

    @property
    def meta(self) -> dict:
        return KINDS[self.kind]

    @property
    def artifact_filename(self) -> str:
        return f"{self.artifact_name}{self.meta['extension']}"

    @property
    def test_filename(self) -> str | None:
        return f"test_{self.artifact_name}.py" if self.meta["needs_test_code"] else None

    def effective_checks(self) -> list[dict]:
        """Mandatory checks first — a malformed or empty artifact should
        fail on that rather than on some downstream check's exception."""
        merged = [dict(c) for c in self.meta["mandatory"]]
        seen = {c["type"] for c in merged}
        for c in self.checks:
            if c["type"] in seen and c["type"] in {m["type"] for m in self.meta["mandatory"]}:
                continue  # don't duplicate a mandatory check
            merged.append(dict(c))
        # pytest_suite needs to know the filename; fill it in centrally so
        # the author can't get it wrong.
        for c in merged:
            if c["type"] == "pytest_suite":
                c["test_filename"] = self.test_filename
        return merged

    def specific_checks(self) -> list[dict]:
        mandatory_types = {m["type"] for m in self.meta["mandatory"]}
        return [c for c in self.checks if c["type"] not in mandatory_types]

    def describe(self) -> str:
        return f"{self.artifact_filename} [{self.kind}] + {len(self.effective_checks())} checks"

    def requirements_brief(self) -> str:
        """The STRUCTURAL half of the contract, stated to the builder up
        front. Deliberately not the whole gate.

        Withholding structure doesn't protect anything — it just burns
        retries on the builder rediscovering key names by trial and
        error, which is exactly how a KPI ticket failed by nesting
        everything under 'contract_metadata'. Acceptance criteria are
        normally handed to the implementer; test-driven development
        hands over the tests outright. The integrity guarantee is that
        the builder did not AUTHOR the gate and cannot EDIT it, not that
        it never saw it.

        regex_present/regex_absent are held back on purpose. Those are
        spot-checks for substance ('does this mention the $189 fee'),
        and a builder shown the pattern can satisfy it by pasting the
        string without doing the surrounding work. Structural
        requirements can't be faked that cheaply — you cannot produce
        six metric definitions or five populated table rows without
        actually writing them. A regex that fails still comes back
        through the repair loop, so nothing is unrecoverable; it just
        isn't pre-gameable."""
        lines = []
        for spec in self.effective_checks():
            t = spec["type"]
            if t == "required_sections":
                lines.append("Required sections (use these as headings):")
                lines += [f"  - {s}" for s in spec["sections"]]
            elif t in ("json_required_keys", "yaml_required_keys"):
                lines.append("Required keys (dotted paths; 'a[].b' means every element of list a has b):")
                lines += [f"  - {k}" for k in spec["keys"]]
            elif t == "min_collection_size":
                lines.append(f"'{spec['path']}' must contain at least {spec.get('count', 3)} entries.")
            elif t == "min_table_rows":
                lines.append(f"Include a table with at least {spec.get('count', 3)} body rows.")
            elif t == "min_words":
                lines.append(f"At least {spec.get('count', 100)} words overall.")
            elif t == "min_words_per_section":
                lines.append(
                    f"Every level-{spec.get('level', 2)} section needs at least "
                    f"{spec.get('count', 40)} words of real content."
                )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "slug": self.slug, "kind": self.kind, "artifact_name": self.artifact_name,
            "checks": self.checks, "test_code": self.test_code,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TicketContract":
        return cls(
            slug=data["slug"], kind=data["kind"], artifact_name=data["artifact_name"],
            checks=data.get("checks", []), test_code=data.get("test_code"),
        )


def slug_for(ticket: dict) -> str:
    """Prefer the upstream system's own subtask id — already stable and
    unique. Fall back to a slugified title."""
    subtask_id = ticket.get("source_subtask_id")
    if subtask_id:
        return str(subtask_id)
    slug = re.sub(r"[^a-z0-9]+", "_", ticket["title"].lower()).strip("_")
    return slug[:60] or "untitled_ticket"


def contract_path(slug: str) -> Path:
    return CONTRACTS_DIR / f"{slug}.json"


def load_contract(slug: str) -> "TicketContract | None":
    path = contract_path(slug)
    if not path.exists():
        return None
    return TicketContract.from_dict(json.loads(path.read_text(encoding="utf-8")))


def freeze_contract(contract: TicketContract) -> Path:
    """Write the contract to disk. From this point the target is fixed:
    every retry and every parallel racer reads the same bytes."""
    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = contract_path(contract.slug)
    path.write_text(json.dumps(contract.to_dict(), indent=2), encoding="utf-8")
    return path


# ---- Deterministic validation of the contract itself -----------------
def validate_contract(contract: TicketContract) -> tuple[bool, str]:
    """Code decides whether the CONTRACT is acceptable, exactly as code
    decides whether the artifact is. No model opinion involved."""
    if contract.kind not in KINDS:
        return False, f"unknown kind '{contract.kind}'"

    meta = contract.meta
    permitted = set(meta["allowed"]) | {m["type"] for m in meta["mandatory"]}
    for spec in contract.checks:
        if spec["type"] not in CHECKS:
            return False, f"unknown check '{spec['type']}'"
        if spec["type"] not in permitted:
            return False, f"check '{spec['type']}' is not valid for kind '{contract.kind}'"

    if len(contract.specific_checks()) < MIN_SPECIFIC_CHECKS:
        return False, (
            f"only {len(contract.specific_checks())} ticket-specific check(s); "
            f"needs at least {MIN_SPECIFIC_CHECKS} or the gate is generic"
        )

    if meta["needs_test_code"]:
        if not contract.test_code:
            return False, f"kind '{contract.kind}' requires test_code"
        try:
            tree = ast.parse(contract.test_code)
        except SyntaxError as exc:
            return False, f"test code does not parse: {exc}"
        test_fns = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
        if len(test_fns) < 3:
            return False, f"only {len(test_fns)} test function(s); needs at least 3"
        imports_target = any(
            (isinstance(n, ast.Import) and any(a.name.split(".")[0] == contract.artifact_name for a in n.names))
            or (isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == contract.artifact_name)
            for n in ast.walk(tree)
        )
        if not imports_target:
            return False, f"tests never import the module under test ('{contract.artifact_name}')"

    # The non-vacuity check, generalised: run the gate against an EMPTY
    # artifact. Two separate questions, because the kind's mandatory
    # checks would otherwise mask a discretionary set that asserts
    # nothing (every artifact kind already rejects an empty file, so the
    # full gate failing proves only that the MANDATORY checks work).
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        workspace = Path(tmp)
        artifact = workspace / contract.artifact_filename
        artifact.write_text("", encoding="utf-8", newline="\n")
        if contract.test_filename and contract.test_code:
            (workspace / contract.test_filename).write_text(
                contract.test_code, encoding="utf-8", newline="\n"
            )
        full_passed, _ = run_checks(artifact, contract.effective_checks())
        # Each specific check on its own: at least one must reject an
        # empty artifact, or the ticket-specific half of the gate is
        # decoration (`min_words: 0`, a regex_absent that never matches).
        specific_results = [
            run_checks(artifact, [spec])[0] for spec in contract.specific_checks()
        ]

    if full_passed:
        return False, "checks PASS against an empty artifact — vacuous contract, measures nothing"
    if specific_results and all(specific_results):
        return False, (
            "every ticket-specific check PASSES against an empty artifact — "
            "the specific half of the gate measures nothing"
        )

    return True, (
        f"{contract.kind}, {len(contract.effective_checks())} checks "
        f"({len(contract.specific_checks())} ticket-specific), fails correctly on an empty artifact"
    )


# ---- Authoring -------------------------------------------------------
def author_contract(client, ticket: dict) -> TicketContract:
    """Ask the reasoning tier for an acceptance contract. Raises
    ContractError when the ticket's deliverable is not a file at all."""
    interaction = client.interactions.create(
        model=CONTRACT_AUTHOR_MODEL,
        system_instruction=CONTRACT_AUTHOR_SYSTEM_INSTRUCTION,
        input=f"Title: {ticket['title']}\n\nDescription:\n{ticket['description']}",
    )
    raw = interaction.output_text.strip()

    # Models still fence JSON sometimes despite being told not to.
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]

    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError as exc:
        raise ContractError(f"contract author returned non-JSON: {exc}") from exc

    if not data.get("buildable", True):
        raise ContractError(data.get("reason", "ticket does not produce a file"))

    kind = data.get("kind")
    if kind not in KINDS:
        raise ContractError(f"contract author chose unknown kind {kind!r}")

    name = data.get("artifact_name") or slug_for(ticket)
    name = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")[:60] or "artifact"

    return TicketContract(
        slug=slug_for(ticket),
        kind=kind,
        artifact_name=name,
        checks=data.get("checks", []),
        test_code=data.get("test_code"),
    )


def contract_for_ticket(ticket: dict, client=None, force: bool = False) -> TicketContract:
    """Load the frozen contract if one exists, otherwise author, validate
    and freeze a new one. This is the only function the router calls."""
    slug = slug_for(ticket)

    if not force:
        existing = load_contract(slug)
        if existing:
            return existing

    if client is None:
        from google import genai
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    contract = author_contract(client, ticket)

    ok, detail = validate_contract(contract)
    if not ok:
        raise ContractError(f"authored contract rejected by validation: {detail}")

    freeze_contract(contract)
    return contract


if __name__ == "__main__":
    # Smoke-test the deterministic half with no API key: a real contract,
    # a vacuous one, and a generic one, to show each gate bites.
    real = TicketContract(
        slug="demo_real", kind="markdown_document", artifact_name="integration_spec",
        checks=[
            {"type": "required_sections", "sections": ["Data Flow", "Field Mapping"]},
            {"type": "min_words_per_section", "count": 30},
        ],
    )
    vacuous = TicketContract(
        slug="demo_vacuous", kind="markdown_document", artifact_name="doc",
        checks=[
            {"type": "regex_absent", "pattern": "zzzz-never-appears", "description": "nothing"},
            {"type": "min_words", "count": 0},
        ],
    )
    generic = TicketContract(
        slug="demo_generic", kind="markdown_document", artifact_name="doc", checks=[],
    )
    for c in (real, vacuous, generic):
        ok, detail = validate_contract(c)
        print(f"{c.slug:15} -> {'ACCEPT' if ok else 'REJECT'}: {detail}")
