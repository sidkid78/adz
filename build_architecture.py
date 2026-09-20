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
import shutil
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
from dependencies import (
    missing_from,
    packages_for_architecture,
    verify_on_npm,
)
from factory_telemetry import NullRunLog, RunLog, new_run_id
from greenfield import (
    PER_TICKET_GATE,
    TargetRepo,
    deno_check_cmd,
    run_integration,
)
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
                 verbose: bool = False, log=None, layer: int = 0) -> tuple[bool, str]:
    import time as _time
    log = log or NullRunLog()
    ticket_started = _time.time()
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
    log.ticket_start(
        ticket["id"], layer, route["model"], contract.required_paths,
        route["max_attempts"],
        {"role": expert.expertise.role, "runs": expert.expertise.total_runs,
         "success_rate": round(expert.expertise.success_rate, 3)} if expert else None,
    )

    agent = ChangesetAgent(model=route["model"], system_instruction=system_instruction)
    files = agent.write(ticket, contract, repo.existing_paths(),
                        dependency_context(repo, ticket, owners))

    last_failure = "agent produced no parseable files"
    for attempt in range(1, route["max_attempts"] + 1):
        attempt_started = _time.time()
        if not files:
            print(f"    attempt {attempt}: no files parsed from response")
            log.attempt_end(ticket["id"], attempt, "no_files",
                            duration=round(_time.time() - attempt_started, 2))
            files = agent.repair(last_failure, contract)
            continue

        written = repo.write_files(files)
        ok, detail = validate_changeset(contract, repo, written)
        if ok:
            gate = repo.run_gate(contract.gate, log=log, ticket=ticket["id"])
            if gate.passed:
                print(f"    attempt {attempt}: GATE PASSED ({len(written)} files)")
                log.attempt_end(ticket["id"], attempt, "pass",
                                duration=round(_time.time() - attempt_started, 2),
                                files_written=len(written))
                repo.commit(f"feat({ticket['id']}): {ticket['title']}")
                if expert is not None:
                    try:
                        expert.record_outcome(ticket["title"], True, ", ".join(written))
                    except Exception:  # noqa: BLE001,S110 - a passing build is not undone by a memory write
                        pass
                log.ticket_end(ticket["id"], True, attempt,
                               round(_time.time() - ticket_started, 2),
                               f"{len(written)} files")
                return True, f"{len(written)} files"
            last_failure = gate.transcript
            print(f"    attempt {attempt}: gate FAILED ({gate.failed_command})")
            log.attempt_end(ticket["id"], attempt, "gate_fail", stage=gate.failed_command,
                            duration=round(_time.time() - attempt_started, 2),
                            files_written=len(written))
        else:
            last_failure = f"Contract not satisfied: {detail}"
            print(f"    attempt {attempt}: contract FAILED ({detail})")
            log.attempt_end(ticket["id"], attempt, "contract_fail", detail=detail,
                            duration=round(_time.time() - attempt_started, 2))

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
    detail = last_failure.splitlines()[-1][:120] if last_failure else "exhausted attempts"
    log.ticket_end(ticket["id"], False, route["max_attempts"],
                   round(_time.time() - ticket_started, 2), detail)
    return False, detail


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
    ap.add_argument("--no-telemetry", action="store_true",
                    help="do not write factory_runs/<id>/events.jsonl")
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
                # A ticket whose deliverable is a Supabase Edge Function is
                # Deno code that tsc never sees, so without this its gate is
                # only "the file exists". Fail it at its own gate rather
                # than three layers later at integration.
                edge = [f for f in c.required_paths if f.startswith("supabase/functions/")]
                if edge and shutil.which("deno"):
                    c.gate = [*PER_TICKET_GATE, deno_check_cmd(edge)]
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

    # Telemetry starts BEFORE the scaffold. It used to start after, which
    # meant a scaffold or baseline-gate failure produced no events at all
    # — and that is exactly the failure you most want recorded, because
    # the run dies before any ticket has explained itself. A missing
    # tsconfig.check.json killed a run in 30s with nothing on the
    # dashboard to show for it.
    log = NullRunLog() if args.no_telemetry else RunLog(run_id=new_run_id(name))
    if not args.no_telemetry:
        print(f"\nTelemetry : {log.events_path.relative_to(REPO_ROOT)}")
        print("Dashboard : python factory_telemetry.py --serve  "
              "-> http://localhost:8777/dashboard.html")
    log.run_start(str(args.architecture), name,
                  [tickets[t] for layer in layers for t in layer if t in contracts],
                  layers)

    # ---- Dependencies the architecture imports -------------------------
    # Resolved BEFORE the scaffold so they land in package.json and the
    # whole tree resolves in ONE npm install. A second install afterwards
    # rewrites the tree and drops optional native bindings — that is how
    # vitest lost its rolldown binary, failing the test step on a repo
    # whose code was fine.
    #
    # A ticket cannot do this itself (write_files refuses package.json),
    # so a missing dependency is unfixable by the agent and burns every
    # retry on "Cannot find module".
    fresh_scaffold = args.fresh or not repo.exists()
    extra_packages: list[str] = []
    candidates = packages_for_architecture(arch)
    if candidates:
        real, unknown = verify_on_npm(candidates)
        extra_packages = real if fresh_scaffold else missing_from(repo.path, real)
        if unknown:
            # Not an error: a name the registry has never heard of is
            # usually illustrative, occasionally a model inventing a
            # library. Either way, do not try to install it.
            print(f"deps     : ignoring {len(unknown)} unknown name(s): {', '.join(unknown)}")
            log.log(f"unknown packages ignored: {', '.join(unknown)}")
        print(f"deps     : {len(extra_packages)} package(s) from the architecture"
              + (f": {', '.join(extra_packages)}" if extra_packages else " (all present)"))
        log.log(f"architecture packages: {', '.join(extra_packages) or 'none needed'}")

    # ---- Scaffold -----------------------------------------------------
    if fresh_scaffold:
        print(f"\nScaffolding {repo.path} ...")
        with log.timed("scaffold", phase="npm install"):
            result = repo.scaffold(force=args.fresh, install=True,
                                   extra_packages=extra_packages)
        if not result.passed:
            print(f"SCAFFOLD FAILED at {result.failed_command}\n{result.transcript[-1500:]}")
            log.integration_step("scaffold", "fail", 0.0,
                                 result.failed_command or "", result.transcript)
            log.run_end([], [t for t in contracts], [], False, "scaffold failed")
            return 1
        if not args.no_supabase:
            sb = repo.supabase_init()
            print(f"supabase: {sb.transcript}")
            log.log(f"supabase: {sb.transcript}")
    elif extra_packages:
        # Existing repo: its package.json predates this architecture.
        with log.timed("install_packages", packages=extra_packages):
            result = repo.install_packages(extra_packages)
        if not result.passed:
            print(f"DEPENDENCY INSTALL FAILED\n{result.transcript[-1200:]}")
            log.run_end([], list(contracts), [], False, "dependency install failed")
            return 1

    # The baseline must be green before any ticket runs, so that every
    # later gate result is attributable to the ticket that just ran.
    baseline = repo.run_gate(PER_TICKET_GATE, log=log, ticket="<baseline>")
    print(f"baseline gate: {'PASS' if baseline.passed else 'FAIL'}")
    log.integration_step("baseline gate", "pass" if baseline.passed else "fail",
                         0.0, "", "" if baseline.passed else baseline.transcript)
    if not baseline.passed:
        print(baseline.transcript[-1500:])
        log.run_end([], [t for t in contracts], [], False, "baseline gate failed")
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
            ok, detail = build_ticket(repo, ticket, contract, owners, args.verbose,
                                      log=log, layer=i)
            (passed if ok else failed).append(tid)
            print(f"    result   : {'PASSED' if ok else 'FAILED'} — {detail}")

    # ---- Integration gate, with repair --------------------------------
    print(f"\n{'=' * 78}\nINTEGRATION GATE (whole repo)\n{'=' * 78}")
    integration = run_integration(repo, start_supabase=not args.no_supabase, log=log)
    print(integration.summary())

    repairs: list[str] = []
    for round_no in range(1, args.integration_rounds + 1):
        if integration.passed:
            break
        print(f"\n--- integration round {round_no}: FAILED at {integration.failed} ---")
        # Never repair code for an environment failure. A transient 502
        # from the local Supabase stack once blamed four tickets, purely
        # because the CLI's output happened to mention their file paths —
        # queueing rewrites of code that was already correct.
        if integration.infra:
            print("    INFRASTRUCTURE failure — not attributable to any ticket.")
            print(f"    {integration.failed} failed after its retries; the code is unchanged.")
            print("    Re-run the integration gate once the environment is healthy.")
            break
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
            log.repair(tid, round_no, ok, detail)
            if ok:
                repairs.append(tid)
                progressed = True
        if not progressed:
            break
        integration = run_integration(repo, start_supabase=False, log=log)
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
    log.run_end(passed, failed, repairs, integration.passed, integration.summary())
    if not args.no_telemetry:
        print(f"TELEMETRY: {log.events_path.relative_to(REPO_ROOT)}")
    print("=" * 78)
    return 0 if integration.passed and not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
