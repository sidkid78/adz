#!/usr/bin/env python3
"""
build_architecture.py — the greenfield software factory

Takes an orch2 technical architecture and builds it into a real,
compiling, tested repo.

  1. ORDER      resolve the planner's dependency DAG into layers. Nothing
                is built before the code it imports exists.
  2. CONTRACT   derive the files each ticket owns, deterministically,
                from the architecture document itself.
  3. BUILD      a specialist writes the whole changeset in one turn,
                with its dependencies' actual source in context.
  4. GATE       the repo's own toolchain judges it: tsc, then vitest.
                Failures go back to the agent verbatim.
  5. COMMIT     a passing changeset is committed. A failing one is
                REVERTED, so the next ticket never compiles against
                half-finished code.
  6. INTEGRATE  once every ticket has landed, the full gate runs again
                over the whole repo.

The loop is the "Request, Validate, Resolve" pattern from notes.md, with
the compiler as the validator — the one reviewer that costs no tokens and
cannot be talked out of its opinion.

Usage:
  python build_architecture.py specs/pm-mcp-server.architecture.json --plan
  python build_architecture.py specs/pm-mcp-server.architecture.json --build
  python build_architecture.py specs/pm-mcp-server.architecture.json --build --only db_schema,mcp_init
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env.local")
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from changesets import (
    SYSTEM_INSTRUCTION,
    ChangesetAgent,
    ChangesetError,
    contract_for_ticket,
    validate_changeset,
)
from greenfield import PER_TICKET_GATE, TargetRepo, run_integration
from import_architecture import build_order

# Cheap tier for boilerplate, reasoning tier for the hard algorithms.
# Same shape as factory_router.COMPLEXITY_ROUTES, but the retry budgets
# are larger: a compiler error is a precise, fixable signal, so extra
# attempts convert into passes far more often than they do for prose.
ROUTES = {
    "low": {"model": "gemini-3.5-flash-lite", "max_attempts": 3},
    "medium": {"model": "gemini-3.8-flash", "max_attempts": 4},
    "high": {"model": "gemini-3.1-pro-preview", "max_attempts": 5},
}


def expert_for(ticket: dict):
    """The persistent expert for this ticket's domain, or None. Learning
    is optional; building is not."""
    try:
        from artifact_agent import EXPERTS_DIR, role_slug
        from meta.agent_expert import AgentExpert
        EXPERTS_DIR.mkdir(parents=True, exist_ok=True)
        return AgentExpert(
            role=role_slug(ticket.get("expertise")),
            base_system_prompt=SYSTEM_INSTRUCTION,
            experts_dir=EXPERTS_DIR,
        )
    except Exception:  # noqa: BLE001
        return None


def dependency_context(repo: TargetRepo, ticket: dict, owners: dict[str, str]) -> str:
    """The actual source of the files this ticket's dependencies produced.

    Progressive disclosure, the same principle as scout/: don't dump the
    repo, disclose precisely the API surface this ticket must compile
    against. A file list alone is not enough — the agent needs the real
    exported names, or it invents plausible ones and tsc rejects them.
    """
    deps = set(ticket.get("dependencies", []))
    if not deps:
        return ""
    blocks = []
    for path, owner in owners.items():
        if owner not in deps:
            continue
        content = repo.read_file(path)
        if content:
            blocks.append(f"--- {path} ---\n{content}")
    if not blocks:
        return ""
    return (
        "## Source of your dependencies (import from these; do not redefine them)\n"
        + "\n\n".join(blocks) + "\n\n"
    )


def build_ticket(repo: TargetRepo, ticket: dict, contract, owners: dict[str, str],
                 verbose: bool = False) -> tuple[bool, str]:
    route = ROUTES.get(ticket.get("complexity", "medium"), ROUTES["medium"])
    expert = expert_for(ticket)

    system_instruction = SYSTEM_INSTRUCTION
    if expert is not None:
        try:
            system_instruction = expert._system_prompt_with_expertise()
        except Exception:  # noqa: BLE001,S110 - expertise is an optimisation; build without it
            pass
        e = expert.expertise
        print(f"    expert   : {e.role} ({e.total_runs} runs, {e.success_rate:.0%} success)")

    print(f"    model    : {route['model']} (up to {route['max_attempts']} attempts)")

    agent = ChangesetAgent(model=route["model"], system_instruction=system_instruction)
    files = agent.write(ticket, contract, repo.existing_paths(),
                        dependency_context(repo, ticket, owners))

    last_failure = "agent produced no parseable files"
    for attempt in range(1, route["max_attempts"] + 1):
        if not files:
            print(f"    attempt {attempt}: no files parsed from response")
            files = agent.repair(last_failure, contract)
            continue

        written = repo.write_files(files)
        ok, detail = validate_changeset(contract, repo, written)
        if ok:
            gate = repo.run_gate(contract.gate)
            if gate.passed:
                print(f"    attempt {attempt}: GATE PASSED ({len(written)} files)")
                repo.commit(f"feat({ticket['id']}): {ticket['title']}")
                if expert is not None:
                    try:
                        expert.record_outcome(ticket["title"], True, ", ".join(written))
                    except Exception:  # noqa: BLE001,S110 - a passing build is not undone by a memory write
                        pass
                return True, f"{len(written)} files"
            last_failure = gate.transcript
            print(f"    attempt {attempt}: gate FAILED ({gate.failed_command})")
        else:
            last_failure = f"Contract not satisfied: {detail}"
            print(f"    attempt {attempt}: contract FAILED ({detail})")

        if verbose:
            print("      " + last_failure[-500:].replace("\n", "\n      "))
        if attempt < route["max_attempts"]:
            files = agent.repair(last_failure, contract)

    # Never leave a failed ticket's files behind: the next ticket in the
    # DAG would compile against them and inherit the failure.
    repo.revert_uncommitted()
    if expert is not None:
        try:
            expert.record_outcome(ticket["title"], False, last_failure[-800:])
        except Exception:  # noqa: BLE001,S110 - ditto; the gate verdict already stands
            pass
    return False, last_failure.splitlines()[-1][:120] if last_failure else "exhausted attempts"


def blame_tickets(failure: str, owners: dict[str, str]) -> list[str]:
    """Which tickets own the files an integration failure names.

    The per-ticket gate cannot catch cross-cutting defects — `tsc` is
    happy with a Next route that exports a helper alongside its
    handlers, and with `./x.js` imports webpack cannot resolve. Those
    only surface at integration, by which time the owning ticket is long
    committed. Blaming by path is what lets the factory send the failure
    back to the ticket that caused it instead of stopping."""
    blamed: list[str] = []
    normalised = failure.replace("\\", "/")
    # Next reports route-contract errors against the types it GENERATES
    # (".next/types/app/api/.../route.ts"), never against the source file
    # that caused them. That path mirrors the source one, so rewriting it
    # back is what lets the failure reach the ticket that owns the route.
    normalised = normalised.replace(".next/types/app/", "src/app/")
    for path, owner in owners.items():
        if owner in blamed:
            continue
        # Match with AND without the extension. Next reports module paths
        # with the extension stripped ("…/analytics/route"), so matching
        # only the exact filename silently blamed nobody and the repair
        # loop did nothing while reporting a failure it could have fixed.
        stem = path.rsplit(".", 1)[0]
        if path in normalised or stem in normalised:
            blamed.append(owner)
    return blamed


def repair_ticket(repo: TargetRepo, ticket: dict, contract, failure: str,
                  max_attempts: int = 3) -> tuple[bool, str]:
    """Re-open a committed ticket to fix an integration failure.

    A fresh agent session, primed with the CURRENT contents of the files
    the ticket owns — the original session is gone, and the files have
    been committed since, so the repair prompt has to carry the code
    rather than assume the model remembers writing it.
    """
    route = ROUTES.get(ticket.get("complexity", "medium"), ROUTES["medium"])
    agent = ChangesetAgent(model=route["model"], system_instruction=SYSTEM_INSTRUCTION)

    current = "\n\n".join(
        f"--- {p} ---\n{repo.read_file(p)}"
        for p in contract.required_paths if repo.read_file(p) is not None
    )
    files = agent._call(
        f"These files are already committed but the INTEGRATION gate rejects them:\n\n"
        f"```\n{failure[-5000:]}\n```\n\n"
        f"## Current contents\n{current}\n\n"
        f"Fix the cause and re-emit every file that must change, complete, in the "
        f"delimiter format. Keep all existing exports that other modules import."
    )

    for attempt in range(1, max_attempts + 1):
        if not files:
            return False, "agent produced no parseable files"
        repo.write_files(files)
        gate = repo.run_gate(PER_TICKET_GATE)
        if gate.passed:
            repo.commit(f"fix({ticket['id']}): satisfy integration gate")
            return True, f"repaired on attempt {attempt}"
        if attempt < max_attempts:
            files = agent.repair(gate.transcript, contract)

    repo.revert_uncommitted()
    return False, "repair exhausted its attempts"


def main() -> int:
    ap = argparse.ArgumentParser(description="Build an orch2 technical architecture into a repo")
    ap.add_argument("architecture", type=Path, help="specs/<name>.architecture.json")
    ap.add_argument("--build", action="store_true", help="actually build (costs tokens)")
    ap.add_argument("--plan", action="store_true", help="show the plan only; no API calls")
    ap.add_argument("--only", help="comma-separated ticket ids to build")
    ap.add_argument("--repo-name", help="target repo name (default: from the spec filename)")
    ap.add_argument("--fresh", action="store_true", help="re-scaffold the target repo from scratch")
    ap.add_argument("--verbose", action="store_true", help="show gate output on failure")
    ap.add_argument("--no-supabase", action="store_true",
                    help="skip starting the local Supabase stack (db reset will be SKIPPED)")
    ap.add_argument("--integration-rounds", type=int, default=2,
                    help="how many times to repair-and-retry the integration gate")
    args = ap.parse_args()

    arch = json.loads(args.architecture.read_text(encoding="utf-8"))
    tickets = {t["id"]: t for t in arch["tickets"]}
    layers = build_order(arch["tickets"])
    selected = set(args.only.split(",")) if args.only else None

    name = args.repo_name or args.architecture.name.split(".")[0]
    repo = TargetRepo(name)

    print(f"\n{'=' * 78}\nARCHITECTURE: {args.architecture.name}\n"
          f"TARGET REPO : {repo.path}\n{'=' * 78}")

    # ---- Contracts, derived before anything is built -----------------
    owners: dict[str, str] = {}   # path -> owning ticket id
    contracts = {}
    for layer in layers:
        for tid in layer:
            try:
                c = contract_for_ticket(tickets[tid], PER_TICKET_GATE, set(owners))
            except ChangesetError as exc:
                print(f"  [{tid}] NO CONTRACT: {exc}")
                continue
            contracts[tid] = c
            for p in c.required_paths:
                owners[p] = tid

    for i, layer in enumerate(layers, 1):
        print(f"\nLayer {i}:")
        for tid in layer:
            c = contracts.get(tid)
            mark = "  " if not selected or tid in selected else "· "
            if c:
                print(f"  {mark}[{tickets[tid]['complexity']:6}] {tid:22} {c.describe()}")
            else:
                print(f"  {mark}[{tickets[tid]['complexity']:6}] {tid:22} (skipped — no contract)")

    if args.plan or not args.build:
        print(f"\n{len(contracts)} ticket(s) contracted, "
              f"{len(owners)} file(s) total. Pass --build to run.")
        return 0

    # ---- Scaffold -----------------------------------------------------
    if args.fresh or not repo.exists():
        print(f"\nScaffolding {repo.path} ...")
        result = repo.scaffold(force=args.fresh, install=True)
        if not result.passed:
            print(f"SCAFFOLD FAILED at {result.failed_command}\n{result.transcript[-1500:]}")
            return 1
        if not args.no_supabase:
            sb = repo.supabase_init()
            print(f"supabase: {sb.transcript}")

        baseline = repo.run_gate(PER_TICKET_GATE)
        print(f"baseline gate: {'PASS' if baseline.passed else 'FAIL'}")
        if not baseline.passed:
            print(baseline.transcript[-1500:])
            return 1

    repo.branch(f"factory/{name}")

    # ---- Build in dependency order -----------------------------------
    passed, failed = [], []
    for i, layer in enumerate(layers, 1):
        print(f"\n{'=' * 78}\nLAYER {i}\n{'=' * 78}")
        for tid in layer:
            if selected and tid not in selected:
                continue
            contract = contracts.get(tid)
            if not contract:
                continue
            ticket = tickets[tid]
            print(f"\n[{tid}] {ticket['title']}")
            print(f"    files    : {', '.join(contract.required_paths)}")
            ok, detail = build_ticket(repo, ticket, contract, owners, args.verbose)
            (passed if ok else failed).append(tid)
            print(f"    result   : {'PASSED' if ok else 'FAILED'} — {detail}")

    # ---- Integration gate, with repair --------------------------------
    print(f"\n{'=' * 78}\nINTEGRATION GATE (whole repo)\n{'=' * 78}")
    integration = run_integration(repo, start_supabase=not args.no_supabase)
    print(integration.summary())

    repairs: list[str] = []
    for round_no in range(1, args.integration_rounds + 1):
        if integration.passed:
            break
        print(f"\n--- integration round {round_no}: FAILED at {integration.failed} ---")
        blamed = blame_tickets(integration.transcript, owners)
        if not blamed:
            print("    no ticket owns a file named in the failure — cannot auto-repair")
            print(integration.transcript[-2000:])
            break
        print(f"    blamed: {', '.join(blamed)}")
        progressed = False
        for tid in blamed:
            if tid not in contracts:
                continue
            ok, detail = repair_ticket(repo, tickets[tid], contracts[tid], integration.transcript)
            print(f"    repair {tid}: {'OK' if ok else 'FAILED'} — {detail}")
            if ok:
                repairs.append(tid)
                progressed = True
        if not progressed:
            break
        integration = run_integration(repo, start_supabase=False)
        print(f"    re-run: {integration.summary()}")

    if not integration.passed:
        print(integration.transcript[-2500:])

    print(f"\n{'=' * 78}")
    print(f"BUILT    : {len(passed)}/{len(passed) + len(failed)} tickets — {', '.join(passed) or 'none'}")
    if failed:
        print(f"FAILED   : {', '.join(failed)}")
    print(f"INTEGRATE: {'PASS' if integration.passed else 'FAIL'} — {integration.summary()}")
    if repairs:
        print(f"REPAIRED : {', '.join(repairs)}")
    print(f"REPO     : {repo.path}")
    print(f"COMMITS  :\n{repo.log(12)}")
    print("=" * 78)
    return 0 if integration.passed and not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
