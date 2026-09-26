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
import atexit
import json
import re
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

from api_surface import api_surface_for
from changesets import (
    SYSTEM_INSTRUCTION,
    ChangesetAgent,
    ChangesetError,
    ModelUnavailable,
    contract_for_ticket,
    deliverable_kind,
    specified_but_unowned,
    validate_changeset,
)
from cost import Ledger
from dependencies import (
    extract_packages,
    missing_from,
    packages_for_architecture,
    types_for,
    verify_on_npm,
)
from factory_telemetry import NullRunLog, RunLog, new_run_id
from greenfield import (
    PER_TICKET_GATE,
    TargetRepo,
    deno_check_cmd,
    failed_migration,
    run_integration,
    safe_console,
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


_ERROR_LINE = re.compile(r"error [A-Z]*\d+|^\s*[✗×]\s|FAIL\s", re.MULTILINE)


def failure_fingerprint(transcript: str) -> frozenset[str]:
    """What the gate objected to, with line numbers stripped.

    Attempts that differ only in where the errors sit are not progress.
    ai_video_pipeline produced ten errors on attempt 1, ten on attempt 2
    and ten on attempt 3 — the same ten, shifted by a line — and then
    burned its remaining attempts before escalating. Comparing raw
    transcripts would call those three different; comparing this calls
    them identical, which is the truth worth acting on.
    """
    out = set()
    for line in transcript.splitlines():
        if not _ERROR_LINE.search(line):
            continue
        # src/x.ts(12,5): error TS2554  ->  src/x.ts: error TS2554
        out.add(re.sub(r"\(\d+,\d+\)", "", line).strip()[:200])
    return frozenset(out)


def committed_tickets(repo: TargetRepo) -> set[str]:
    """Ticket ids with a `feat(<id>):` commit — written only after the
    ticket's gate passed, so a crashed run can resume past them."""
    return set(re.findall(r"^[0-9a-f]+ feat\(([^)]+)\):", repo.log(limit=500), re.MULTILINE))


def protected_paths(repo: TargetRepo, owners: dict[str, str], tid: str) -> dict[str, str]:
    """Files ticket `tid` may not touch: the scaffold's, and every file the
    plan assigns to another ticket — built yet or not. A ticket's own
    required files stay writable even when the scaffold seeded them
    (pm-mcp-server's mcp_init legitimately owns src/index.ts).

    Anything else — new helpers, the ticket's own tests — stays open, so
    this never refuses a file only this ticket has ever written."""
    mine = {p for p, owner in owners.items() if owner == tid}
    guarded = {p: "the scaffold" for p in repo.scaffold_paths() if p not in mine}
    guarded.update({p: f"ticket {owner}" for p, owner in owners.items() if owner != tid})
    return guarded


def build_ticket(repo: TargetRepo, ticket: dict, contract, owners: dict[str, str],
                 verbose: bool = False, log=None, layer: int = 0,
                 ledger: "Ledger | None" = None) -> tuple[bool, str]:
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

    # What the packages THIS ticket imports actually export. The
    # architecture may target an older major version — it specified
    # Inngest v3's 3-argument createFunction against an installed v4 —
    # and without this the builder rewrites the same stale API on every
    # retry, because the architecture keeps confirming it.
    surface = api_surface_for(
        repo.path, sorted(extract_packages(ticket.get("architecture", ""))),
    )
    agent = ChangesetAgent(model=route["model"], system_instruction=system_instruction)
    files = agent.write(ticket, contract, repo.existing_paths(),
                        dependency_context(repo, ticket, owners), surface)

    last_failure = "agent produced no parseable files"
    seen_failures: list[frozenset[str]] = []
    guarded = protected_paths(repo, owners, ticket["id"])
    for attempt in range(1, route["max_attempts"] + 1):
        attempt_started = _time.time()
        # The call that produced `files` has already happened — either
        # agent.write() before the loop or agent.repair() at the end of
        # the previous turn — so its usage belongs to THIS attempt.
        if ledger is not None and getattr(agent, "last_usage", None):
            ledger.record(ticket["id"], attempt,
                          "build" if attempt == 1 else "repair", agent.last_usage)
        if not files:
            print(f"    attempt {attempt}: no files parsed from response")
            log.attempt_end(ticket["id"], attempt, "no_files",
                            duration=round(_time.time() - attempt_started, 2))
            files = agent.repair(last_failure, contract)
            continue

        # A refused write is a failed attempt the agent is told about, not a
        # crash: write_files raising here used to take the whole run down.
        try:
            written = repo.write_files(files, protected=guarded)
        except ValueError as refused:
            last_failure = str(refused)
            print(f"    attempt {attempt}: write refused ({refused})")
            log.attempt_end(ticket["id"], attempt, "write_refused", detail=str(refused)[:300],
                            duration=round(_time.time() - attempt_started, 2))
            if attempt < route["max_attempts"]:
                files = agent.repair(last_failure, contract)
            continue
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
            # Stop when the attempts stop moving. Two identical failure
            # sets in a row means the agent cannot see the cause from
            # what it is being shown, and the remaining attempts will
            # produce the same transcript at a different line number.
            # Escalating early is not giving up: it is declining to pay
            # three more times for the same answer.
            fingerprint = failure_fingerprint(gate.transcript)
            if fingerprint and seen_failures and fingerprint == seen_failures[-1]:
                print(f"    attempt {attempt}: no progress since attempt "
                      f"{attempt - 1} ({len(fingerprint)} identical failures) — escalating")
                log.attempt_end(ticket["id"], attempt, "no_progress",
                                stage=gate.failed_command,
                                duration=round(_time.time() - attempt_started, 2))
                break
            seen_failures.append(fingerprint)
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
    # `next build` names ROUTES, not files: "Failed to collect page data
    # for /api/boardroom/debate". With no path in the transcript, blame
    # found nobody and a fixable failure stopped the run. Map each route
    # it names back to the page/route file that serves it.
    named_routes = set(re.findall(r"(?:data|configuration) for (/[\w\-/\[\]]*)", normalised))
    if named_routes:
        for path in owners:
            if _route_of(path) in named_routes:
                normalised += f"\n{path}"
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


def _route_of(path: str) -> str | None:
    """The URL a src/app page or route file serves, or None. Route groups
    `(x)` and slots `@x` are not part of the URL."""
    m = re.match(r"src/app/(.*?)/?(?:page|route)\.tsx?$", path)
    if not m:
        return None
    segments = [s for s in m.group(1).split("/") if s and not s.startswith(("(", "@"))]
    return "/" + "/".join(segments)


def repair_ticket(repo: TargetRepo, ticket: dict, contract, failure: str,
                  max_attempts: int = 3, log=None,
                  ledger: "Ledger | None" = None,
                  owners: dict[str, str] | None = None) -> tuple[bool, str, dict]:
    """Re-open a committed ticket to fix an integration failure.

    A fresh agent session, primed with the CURRENT contents of the files
    the ticket owns — the original session is gone, and the files have
    been committed since, so the repair prompt has to carry the code
    rather than assume the model remembers writing it.

    Returns timing alongside the verdict. A 42-minute run once spent
    about eleven minutes somewhere no event accounted for, and the repair
    rounds were the only phase nothing timed. Model time and gate time
    are split because they call for different fixes: slow model calls
    argue for a cheaper repair tier, a slow gate for a narrower one.
    """
    import time as _time
    started = _time.time()
    stats: dict = {"attempts": 0, "model_seconds": 0.0, "gate_seconds": 0.0}
    route = ROUTES.get(ticket.get("complexity", "medium"), ROUTES["medium"])
    agent = ChangesetAgent(model=route["model"], system_instruction=SYSTEM_INSTRUCTION)

    def ask(call, *args):
        t0 = _time.time()
        out = call(*args)
        stats["model_seconds"] += _time.time() - t0
        if ledger is not None and getattr(agent, "last_usage", None):
            ledger.record(ticket["id"], stats["attempts"] + 1, "integration_repair",
                          agent.last_usage)
        return out

    def done(ok: bool, detail: str) -> tuple[bool, str, dict]:
        stats["duration"] = _time.time() - started
        return ok, detail, {k: round(v, 2) if isinstance(v, float) else v
                            for k, v in stats.items()}

    current = "\n\n".join(
        f"--- {p} ---\n{repo.read_file(p)}"
        for p in contract.required_paths if repo.read_file(p) is not None
    )
    files = ask(
        agent._call,
        f"These files are already committed but the INTEGRATION gate rejects them:\n\n"
        f"```\n{failure[-5000:]}\n```\n\n"
        f"## Current contents\n{current}\n\n"
        f"Fix the cause and re-emit every file that must change, complete, in the "
        f"delimiter format. Keep all existing exports that other modules import."
    )

    for attempt in range(1, max_attempts + 1):
        stats["attempts"] = attempt
        if not files:
            return done(False, "agent produced no parseable files")
        # A repair fixes code the gate rejected. It does not get to mint
        # new tests: that is how an earlier one "fixed" reachability.
        try:
            repo.write_files(files, allow_new_tests=False,
                             protected=protected_paths(repo, owners or {}, ticket["id"]))
        except ValueError as refused:
            files = ask(agent.repair, str(refused), contract)
            continue
        t0 = _time.time()
        gate = repo.run_gate(PER_TICKET_GATE, log=log, ticket=f"{ticket['id']} (repair)")
        stats["gate_seconds"] += _time.time() - t0
        if gate.passed:
            repo.commit(f"fix({ticket['id']}): satisfy integration gate")
            return done(True, f"repaired on attempt {attempt}")
        if attempt < max_attempts:
            files = ask(agent.repair, gate.transcript, contract)

    repo.revert_uncommitted()
    return done(False, "repair exhausted its attempts")


def main() -> int:
    safe_console()
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
    ap.add_argument("--code-anyway", action="store_true",
                    help="build even when no ticket names a file (a document architecture)")
    ap.add_argument("--resume", action="store_true",
                    help="skip tickets already committed in the target repo (after a crash)")
    args = ap.parse_args()
    if args.resume and args.fresh:
        ap.error("--resume keeps the existing repo; --fresh deletes it. Pick one.")

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

    # Architectures draw the project layout as a tree. Anything drawn
    # there and owned by nobody is work the planner asked for that this
    # run will silently skip — which is how a build shipped with no `/`
    # route while its own architecture specified app/(marketing)/page.tsx.
    # Said BEFORE the build rather than discovered by an integration gate
    # afterwards.
    unowned = specified_but_unowned(arch["tickets"], set(owners))
    if unowned:
        routes = [p for p in unowned if p.endswith(("page.tsx", "page.ts", "route.ts"))]
        print(f"\nUNBUILT  : {len(unowned)} file(s) the architecture's directory "
              f"trees name but no ticket owns")
        for path in (routes or unowned)[:8]:
            print(f"           {path}")
        if routes:
            print("           ^ these are ROUTES — without them the app has no "
                  "such pages, whatever else builds.")

    # Said BEFORE anything spends tokens. A consulting engagement once
    # planned as five buildable tickets, every path a fallback guess, and
    # the plan printed "5 ticket(s) contracted" as if all was well.
    kind, why = deliverable_kind(arch["tickets"])
    if kind == "document":
        source = arch.get("source") or "<orch2 execution>.json"
        print(f"\nDOCUMENT : this is not a code architecture — {why}.")
        print("           The deliverable is prose; build it with the document factory:")
        print(f"           python run_factory.py {source} --build")
        if args.build and not args.code_anyway:
            print("REFUSING : --build on a document architecture "
                  "(pass --code-anyway to override)")
            return 1

    if args.plan or not args.build:
        print(f"\n{len(contracts)} ticket(s) contracted, "
              f"{len(owners)} file(s) total. Pass --build to run.")
        return 0

    # One build per workspace. A previous run's agent threads can outlive
    # the process that started them (a cancelled build kept writing files
    # for another 30 seconds), and those writes land in a workspace the
    # NEXT run has just scaffolded — where they get swept into the
    # scaffold commit and fail a baseline gate nobody could explain.
    ok, why = repo.acquire_lock()
    if not ok:
        print(f"REFUSING : {why}")
        return 1
    atexit.register(repo.release_lock)

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
        # A package that ships no .d.ts is a failure the agent CANNOT
        # fix: it gets TS7016 and write_files refuses package.json, so
        # every retry burns on a problem outside every file it owns.
        # canvas-confetti cost a ticket two attempts that way, and
        # web-push would have cost the next one more.
        real += types_for(real)
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

    # A ticket counts as built when its own commit is in the repo — the
    # commit only happens after its gate passed, so it is the evidence.
    already_built = committed_tickets(repo) if args.resume else set()
    if already_built:
        print(f"resume   : {len(already_built)} ticket(s) already committed: "
              f"{', '.join(sorted(already_built))}")

    # ---- Build in dependency order -----------------------------------
    passed, failed = [], []
    db_types_done = False
    ledger = Ledger()
    for i, layer in enumerate(layers, 1):
        print(f"\n{'=' * 78}\nLAYER {i}\n{'=' * 78}")
        for tid in layer:
            if selected and tid not in selected:
                continue
            contract = contracts.get(tid)
            if not contract:
                continue
            ticket = tickets[tid]
            if tid in already_built:
                print(f"\n[{tid}] already committed — skipped (--resume)")
                passed.append(tid)
                continue
            print(f"\n[{tid}] {ticket['title']}")
            print(f"    files    : {', '.join(contract.required_paths)}")
            try:
                ok, detail = build_ticket(repo, ticket, contract, owners, args.verbose,
                                          log=log, layer=i, ledger=ledger)
            except ModelUnavailable as exc:
                # The API failed, not the code: fail this ticket, keep the run.
                repo.revert_uncommitted()
                ok, detail = False, f"model API unavailable after retries (infrastructure): {exc}"
                log.ticket_end(tid, False, 0, 0.0, detail[:300])
            ledger.settle(tid, ok)
            (passed if ok else failed).append(tid)
            print(f"    result   : {'PASSED' if ok else 'FAILED'} — {detail}")

        # Once the migrations exist, let the DATABASE state the Database
        # type. Agents hand-writing it omit postgrest's required
        # `Relationships` key, which silently collapses every row to
        # `never` and produces errors only at the use sites — a ticket
        # burned five attempts on that, none of them in the file at
        # fault. Generated here rather than at scaffold time because the
        # migrations are a ticket's output, not the scaffold's.
        if not db_types_done and not args.no_supabase and any(
            owners.get(pth) in layer
            for c in contracts.values() for pth in c.required_paths
            if pth.startswith("supabase/migrations/")
        ):
            with log.timed("db types", phase="supabase gen types"):
                started = repo.supabase_start()
                # A migration that fails to apply is the schema ticket's bug,
                # and every later ticket pays for it: with no generated
                # Database type, rows resolve to `never` (28 errors in a
                # ticket that did nothing wrong). Repair it NOW, then retry.
                bad = failed_migration(started.transcript) if not started.passed else None
                owner = owners.get(bad or "")
                if bad and owner in contracts:
                    print(f"db types : migration {bad} does not apply — repairing {owner}")
                    try:
                        ok, detail, stats = repair_ticket(
                            repo, tickets[owner], contracts[owner], started.transcript,
                            log=log, ledger=ledger, owners=owners)
                    except ModelUnavailable as exc:
                        repo.revert_uncommitted()
                        ok, detail = False, f"model API unavailable (infrastructure): {exc}"
                        stats = {"duration": 0.0, "model_seconds": 0.0,
                                 "gate_seconds": 0.0, "attempts": 0}
                    log.repair(owner, 0, ok, detail, **stats)
                    print(f"    repair {owner}: {'OK' if ok else 'FAILED'} — {detail}")
                    if ok:
                        started = repo.supabase_start()
                gen = repo.generate_db_types() if started.passed else started
            if gen.passed:
                print(f"db types : OK — {gen.transcript}")
            else:
                # The whole transcript, not its last line: supabase
                # ends every failure with "rerun with --debug",
                # which is the one line that says nothing.
                print(f"db types : SKIPPED — {gen.failed_command}")
                print(f"    {gen.transcript[-600:]}")
            log.log(f"db types: {'ok' if gen.passed else 'skipped'}")
            # Not fatal. A ticket can still hand-write the type; it just
            # no longer has to, and the failure is visible rather than
            # showing up as ten `never` errors three tickets later.
            db_types_done = gen.passed

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
            try:
                ok, detail, stats = repair_ticket(repo, tickets[tid], contracts[tid],
                                                  integration.transcript, log=log, ledger=ledger,
                                                  owners=owners)
            except ModelUnavailable as exc:
                repo.revert_uncommitted()
                ok, detail = False, f"model API unavailable (infrastructure): {exc}"
                stats = {"duration": 0.0, "model_seconds": 0.0, "gate_seconds": 0.0, "attempts": 0}
            print(f"    repair {tid}: {'OK' if ok else 'FAILED'} — {detail} "
                  f"({stats['duration']:.0f}s: model {stats['model_seconds']:.0f}s, "
                  f"gate {stats['gate_seconds']:.0f}s, {stats['attempts']} attempt(s))")
            log.repair(tid, round_no, ok, detail, **stats)
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
    print(ledger.report())
    ledger.write(REPO_ROOT / "factory_runs" / log.run_id / "cost.json"
                 if not args.no_telemetry else REPO_ROOT / "factory_runs" / "last_cost.json")
    log.run_end(passed, failed, repairs, integration.passed, integration.summary())
    if not args.no_telemetry:
        print(f"TELEMETRY: {log.events_path.relative_to(REPO_ROOT)}")
    print("=" * 78)
    return 0 if integration.passed and not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
