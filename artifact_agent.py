"""
artifact_agent.py — the builder, specialised per ticket and improving over time

This is where meta/ stops being a demo and becomes part of the factory.

Three pieces were already in the repo, built but not connected to
anything that runs real work:

  meta/meta_prompt_agent.py  compile_worker_prompt(role, tools, hooks)
                             turns a role description into a full
                             8-section specialist system prompt.
  meta/agent_expert.py       AgentExpert persists success/failure counts
                             and distilled lessons per role in
                             meta/experts/<role>.yaml, and folds them
                             into its system prompt on later runs.
  blueprints/blueprints.py   establishes the rule that an expert's
                             outcome comes from the NEXT deterministic
                             step's exit code, never the agent's claim.

Every ticket from an orchestrator report already carries the field that
makes this usable: `source_expertise`, e.g. "HVAC Field Dispatch
Operations & AI Booking Systems". That is precisely the `worker_role`
compile_worker_prompt wants. So:

  ticket.source_expertise -> compiled specialist prompt (cached on disk)
                          -> AgentExpert for that role (accumulated lessons)
                          -> builds the artifact, repairs against the gate
                          -> gate's verdict -> expert.record_outcome()

agent_expert.py's own __main__ notes that its success flag is hardcoded
"for demo purposes" and that a real pipeline MUST take it from a
deterministic check. The contract gate is that check. Connecting the two
is what makes the factory learn: a dispatch-workflow ticket that fails
its gate today leaves a lesson that the next dispatch-workflow ticket
starts with.

Prompt compilation is planning-tier and expensive, so compiled prompts
are cached to meta/worker_prompts/<role>.md and reused.
"""

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from google import genai

from artifact_kinds import KINDS

WORKER_PROMPTS_DIR = REPO_ROOT / "meta" / "worker_prompts"
EXPERTS_DIR = REPO_ROOT / "meta" / "experts"

# Falls back to this when a ticket carries no expertise hint at all.
GENERIC_ROLE = "general_delivery"


def role_slug(expertise: str | None) -> str:
    if not expertise:
        return GENERIC_ROLE
    slug = re.sub(r"[^a-z0-9]+", "_", expertise.lower()).strip("_")
    return slug[:60] or GENERIC_ROLE


def compiled_prompt_for_role(expertise: str | None, kind: str, client=None) -> str:
    """Compile (or reuse) the specialist system prompt for this role.

    Cached on disk because compile_worker_prompt is a pro-tier call and
    the role repeats across tickets — five tickets from one report often
    share two or three expertise domains."""
    role = role_slug(expertise)
    cached = WORKER_PROMPTS_DIR / f"{role}.md"
    if cached.exists():
        return strip_outer_fence(cached.read_text(encoding="utf-8"))

    if not expertise:
        return _fallback_prompt(kind)

    try:
        from meta.meta_prompt_agent import compile_worker_prompt
        WORKER_PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
        # has_hooks=False: this factory's determinism lives in the
        # contract gate, not in prompt-declared hooks.
        compiled = compile_worker_prompt(
            worker_role=f"{expertise} — producing {KINDS[kind]['description']}",
            required_tools=[],
            has_hooks=False,
            save_as=role,
        )
        return strip_outer_fence(compiled)
    except Exception as exc:  # noqa: BLE001 - compilation is an optimisation, not a requirement
        # Surfaced rather than swallowed: a silently-failing compile is
        # how this path sat dead behind an invalid model id.
        print(f"(worker prompt compile failed for '{role}': {type(exc).__name__}: {exc}; "
              f"using fallback prompt)")
        return _fallback_prompt(kind)


def _fallback_prompt(kind: str) -> str:
    return (
        f"You are a senior specialist producing {KINDS[kind]['description']}. "
        "You write complete, concrete, immediately usable deliverables. "
        "No pleasantries, no preamble."
    )


def expert_for_ticket(ticket: dict, kind: str, client=None):
    """The AgentExpert for this ticket's domain, or None if the expert
    machinery is unavailable (missing key, import problem). The factory
    must still build when learning is unavailable — the gate does not
    depend on it."""
    try:
        from meta.agent_expert import AgentExpert
        role = role_slug(ticket.get("source_expertise"))
        base = compiled_prompt_for_role(ticket.get("source_expertise"), kind, client)
        EXPERTS_DIR.mkdir(parents=True, exist_ok=True)
        return AgentExpert(role=role, base_system_prompt=base, experts_dir=EXPERTS_DIR)
    except Exception:  # noqa: BLE001 - learning is optional, building is not
        return None


def system_prompt_for(expert, ticket: dict, kind: str) -> str:
    """The expertise-augmented prompt when an expert exists, otherwise
    the plain compiled/fallback one."""
    if expert is not None:
        # AgentExpert exposes this as a private helper; it is the whole
        # point of the class, so use it rather than duplicating it.
        return expert._system_prompt_with_expertise()
    return compiled_prompt_for_role(ticket.get("source_expertise"), kind)


# ---- Output cleaning --------------------------------------------------
_FULL_FENCE = re.compile(r"\A```[a-zA-Z0-9_+-]*\n(.*)\n```\s*\Z", re.DOTALL)


def strip_outer_fence(text: str) -> str:
    """Models wrap whole deliverables in a code fence even when told not
    to. Strip ONLY a fence that encloses the entire output — a markdown
    document legitimately contains fences of its own, and those must
    survive untouched."""
    m = _FULL_FENCE.match(text.strip())
    return m.group(1) if m else text


class ArtifactBuildAgent:
    """One build session for one ticket, against one frozen contract.

    Session continuity is load-bearing: the repair loop depends on the
    model remembering the artifact it just produced, so the failure log
    alone is enough context to fix it. Same previous_interaction_id
    pattern as gemini_build_agent.py.
    """

    def __init__(self, model: str, contract, system_instruction: str):
        self.client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.model = model
        self.contract = contract

        meta = KINDS[contract.kind]
        rules = (
            f"{system_instruction}\n\n"
            f"# Output contract\n"
            f"You are producing exactly one file: {contract.artifact_filename} "
            f"({meta['description']}).\n"
            "- Return ONLY the raw contents of that file.\n"
            "- Do NOT wrap the whole response in a code fence.\n"
            "- Do NOT add commentary before or after the file contents.\n"
            "- Leave nothing unfinished: no TODO, TBD, FIXME, bracketed\n"
            "  placeholders or 'insert X here'. Every value must be a real one.\n"
            "- An automated checker validates this file and you cannot see or\n"
            "  change it. You will see its failure output if it rejects you.\n"
        )

        # The structural requirements, stated up front. See
        # TicketContract.requirements_brief() for why these are disclosed
        # and the regex spot-checks are not.
        brief = contract.requirements_brief()
        if brief:
            rules += f"\n# Structural requirements (the checker enforces these)\n{brief}\n"
        interaction = self.client.interactions.create(model=model, input=rules)
        self._last_interaction_id = interaction.id

    def _call(self, text: str) -> str:
        interaction = self.client.interactions.create(
            model=self.model, input=text, previous_interaction_id=self._last_interaction_id,
        )
        self._last_interaction_id = interaction.id
        return strip_outer_fence(interaction.output_text.strip())

    def write(self, spec: str) -> str:
        return self._call(
            f"{spec}\n\nProduce the complete contents of {self.contract.artifact_filename} now."
        )

    def fix(self, failure_log: str) -> str:
        return self._call(
            f"The automated checker REJECTED your file:\n\n{failure_log}\n\n"
            f"Fix exactly what it reported and return the complete corrected "
            f"contents of {self.contract.artifact_filename}. Return the whole "
            f"file, not a diff or an excerpt."
        )
