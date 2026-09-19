#!/usr/bin/env python3
"""
run_factory.py — the factory: specs in, verified artifacts out

Point it at specs/ and it runs every stage for everything it finds:

  1. INTAKE     spec_intake.py turns each file into tickets, preferring
                deterministic parsing and only using a model for prose.
  2. CONTRACT   ticket_contracts.py decides what FILE each ticket must
                produce and the deterministic checks it must pass, then
                freezes that to contracts/<slug>.json.
  3. BUILD      factory_router.py routes on complexity, builds with a
                specialist whose system prompt is compiled from the
                ticket's own expertise domain and carries the lessons
                that expert learned on previous tickets.
  4. GATE       the contract's checks decide pass/fail. Failures go back
                to the builder as feedback; the verdict goes to the
                expert's permanent memory.
  5. DELIVER    passing artifacts land in drops/outbox/factory/.

Usage:
  python run_factory.py                       # everything in specs/
  python run_factory.py specs/nfo.json        # one file
  python run_factory.py --plan                # intake + routing only, no API calls
  python run_factory.py --build               # actually build (costs tokens)
  python run_factory.py --ticket 4 --build    # one ticket by number
  python run_factory.py --force-contracts     # re-author, ignore frozen ones

Contract authoring is on by default because it is what makes the plan
meaningful; BUILDING is opt-in because it is the expensive stage.
"""

import argparse
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

from artifact_agent import role_slug
from spec_intake import ingest_path
from ticket_contracts import ContractError, contract_for_ticket, load_contract, slug_for

OUTBOX = REPO_ROOT / "drops" / "outbox" / "factory"


def route_preview(ticket: dict) -> str:
    """What the router WILL do, resolved without calling it."""
    from factory_router import COMPLEXITY_ROUTES

    complexity = ticket.get("source_complexity")
    if complexity not in COMPLEXITY_ROUTES:
        return "classify_ticket() (no upstream complexity — costs a classifier call)"
    route = COMPLEXITY_ROUTES[complexity]
    bits = [f"build={route['build_model']}"]
    if route.get("planning_model"):
        bits.append(f"plan={route['planning_model']}")
    bits.append(f"{route['parallel_attempts']} racer(s)")
    bits.append(f"{route['max_retries']} retries")
    return f"{complexity.upper()} -> " + ", ".join(bits)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the ADZ factory over a spec file or directory")
    parser.add_argument("target", nargs="?", type=Path, default=REPO_ROOT / "specs",
                        help="spec file or directory (default: specs/)")
    parser.add_argument("--build", action="store_true", help="run the build loop (costs tokens/sandboxes)")
    parser.add_argument("--plan", action="store_true", help="intake + routing only; no API calls at all")
    parser.add_argument("--force-contracts", action="store_true", help="re-author contracts even if frozen")
    parser.add_argument("--ticket", type=int, help="process a single ticket by its number in the listing")
    parser.add_argument("--limit", type=int, help="process at most N tickets")
    args = parser.parse_args()

    if not args.target.exists():
        print(f"Not found: {args.target}")
        return 1

    # ---- Stage 1: intake ---------------------------------------------
    print(f"\n{'=' * 78}\nINTAKE: {args.target}\n{'=' * 78}")
    results = ingest_path(args.target, allow_model=not args.plan)

    tickets: list[dict] = []
    for r in results:
        detail = r.note or f"{len(r.tickets)} ticket(s)"
        print(f"  {r.path.name:32} {r.method:30} {detail}")
        tickets.extend(r.tickets)

    if not tickets:
        print("\nNo tickets found. Nothing to build.")
        return 0

    if args.ticket:
        if not 1 <= args.ticket <= len(tickets):
            print(f"--ticket must be between 1 and {len(tickets)}")
            return 1
        tickets = [tickets[args.ticket - 1]]
    elif args.limit:
        tickets = tickets[: args.limit]

    print(f"\n{'=' * 78}\n{len(tickets)} TICKET(S)\n{'=' * 78}")

    accepted = refused = errored = built = escalated = 0
    kinds: dict[str, int] = {}

    for i, ticket in enumerate(tickets, 1):
        complexity = (ticket.get("source_complexity") or "unrated").upper()
        print(f"\n[{i}] {ticket['title']}")
        print(f"    spec       : {len(ticket['description']):,} chars, complexity {complexity}")
        print(f"    expert     : {role_slug(ticket.get('source_expertise'))}")
        print(f"    route      : {route_preview(ticket)}")

        if args.plan:
            continue

        # ---- Stage 2: contract ---------------------------------------
        cached = load_contract(slug_for(ticket)) is not None and not args.force_contracts
        try:
            contract = contract_for_ticket(ticket, force=args.force_contracts)
        except ContractError as exc:
            refused += 1
            print(f"    contract   : REFUSED — {exc}")
            print("                 -> escalated to a human; nothing built, nothing faked")
            continue
        except Exception as exc:  # noqa: BLE001 - one bad ticket must not abort the batch
            errored += 1
            print(f"    contract   : ERROR — {type(exc).__name__}: {exc}")
            continue

        accepted += 1
        kinds[contract.kind] = kinds.get(contract.kind, 0) + 1
        origin = "cached" if cached else "authored + validated"
        print(f"    contract   : {contract.describe()} ({origin})")
        for spec in contract.specific_checks():
            print(f"                 - {spec['type']}: "
                  f"{ {k: v for k, v in spec.items() if k != 'type'} }")

        # ---- Stages 3-5: build, gate, deliver ------------------------
        if not args.build:
            continue

        from factory_router import handle_ticket

        result = handle_ticket(ticket, contract=contract)
        if result:
            built += 1
            OUTBOX.mkdir(parents=True, exist_ok=True)
            out = OUTBOX / contract.artifact_filename
            out.write_text(result, encoding="utf-8", newline="\n")
            print(f"    result     : PASSED -> {out.relative_to(REPO_ROOT)}")
        else:
            escalated += 1
            print("    result     : ESCALATED to human")

    # ---- Summary ------------------------------------------------------
    print(f"\n{'=' * 78}")
    if args.plan:
        print(f"PLAN ONLY: {len(tickets)} ticket(s) routed. No API calls made.")
    else:
        kind_summary = ", ".join(f"{n}x {k}" for k, n in sorted(kinds.items())) or "none"
        print(f"CONTRACTS: {accepted} accepted ({kind_summary})"
              f"{f', {refused} refused' if refused else ''}"
              f"{f', {errored} errored' if errored else ''}.")
        if args.build:
            print(f"BUILDS   : {built} passed the gate, {escalated} escalated.")
            if built:
                print(f"DELIVERED: {OUTBOX.relative_to(REPO_ROOT)}")
        else:
            print("BUILDS   : skipped (pass --build to run them).")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
