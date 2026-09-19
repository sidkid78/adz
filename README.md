# ADZ — Autonomous Developer Zone

A software factory. It takes a **technical architecture** and builds it into a **real repository
that compiles, typechecks, passes its tests, builds, and applies its own database migrations** —
with no human in the loop between the spec going in and the commits coming out.

The rule the whole system is built around:

> **Code decides pass/fail, never the agent.**
> An agent's claim that it succeeded is worth nothing. The compiler's opinion is worth
> everything, costs no tokens, and cannot be argued with.

---

## Proven run

Input: an 8-subtask Next.js + Supabase + FastMCP architecture (a "Project Management MCP
Server"), produced by a separate multi-agent planner.

```text
BUILT    : 8/8 tickets — db_schema, mcp_init, kickoff_workflow, planning_engine,
                         risk_comm_system, monitoring_analytics,
                         resource_optimization, nextjs_frontend
FILES    : 26 across 6 dependency layers
INTEGRATE: PASS — typecheck, tests, next build, route typegen,
                  route contracts, supabase db reset
```

Output: [sidkid78/pm-mcp-](https://github.com/sidkid78/pm-mcp-) — 13 commits, one per ticket
plus repairs, including a real critical-path scheduler (forward/backward pass, slack, buffers)
that the builder wrote its own tests for.

---

## Quick start

```powershell
# 1. Import an architecture (deterministic; no API calls)
python import_architecture.py --list <orch2>/backend/workflow_logs/executions
python import_architecture.py <execution>.json --name pm-mcp-server

# 2. See the plan: dependency layers, files each ticket owns (no API calls)
python build_architecture.py specs/pm-mcp-server.architecture.json --plan

# 3. Build it
python build_architecture.py specs/pm-mcp-server.architecture.json --build
```

Output lands in `factory_workspace/<name>/` as its own independent git repo.

**Requires:** Python 3.12 + [uv](https://docs.astral.sh/uv/), Node 20+, git.
Docker and the Supabase CLI are optional — without them the `supabase db reset` step reports
`SKIPPED` with a reason rather than silently passing.

Create `.env.local`:

```env
GEMINI_API_KEY="..."
E2B_API_KEY="..."   # optional, for cloud sandboxes
```

---

## How it works

```text
architecture ─┐
              │  import_architecture.py   preserve dependencies, output_format and
              │                           complexity; FAIL LOUDLY on a short export
              ▼
         tickets ── build_order()         Kahn layers — nothing is built before the
              │                           code it imports exists
              ▼
       changesets.py                      derive the files each ticket owns,
              │                           deterministically, from the architecture doc
              ▼
        greenfield.py                     scaffold a real git repo; npm install once
              │
              ▼
      ChangesetAgent                      a specialist writes the ticket's whole
              │                           changeset in one turn, with its dependencies'
              │                           actual source in context
              ▼
   ┌─ per-ticket gate ─┐                  tsc (source only) + vitest — seconds, so it
   │  pass → commit    │                  runs on every repair attempt
   │  fail → feedback ─┘                  the compiler's error goes back verbatim
   │  exhausted → REVERT                  a failed ticket leaves nothing behind
   ▼
  integration gate                        next build · route typegen · route contracts
   │                                      · supabase db reset — minutes, once
   ▼
  blame + repair                          map the failure to the ticket that owns the
                                          file, reopen it, re-run
```

### Why each piece exists

**Dependency ordering.** The planner already knows `planning_engine` needs `kickoff_workflow`'s
types. Building in arbitrary order and hoping the compiler forgives you is not a factory.

**Contracts are derived, not invented.** Architecture documents already name the files they
specify — in a heading (`### 2. Core TypeScript Interfaces (src/types/index.ts)`) or a `// src/…`
header comment on the code itself. A model asked to invent the file list would invent a
*different* list, and the point is to build the architecture that was designed.

**First writer wins.** Ownership is assigned in dependency order, so later tickets are told to
import a file, not rewrite it. Without this a late ticket clobbers an earlier one's types and
the failure surfaces as an unrelated error three tickets later.

**Staged gating.** `npm install` and a framework build are slow. Install once at scaffold, run
the fast source checks per ticket, run the expensive cross-cutting checks once at the end.
That's what real CI does, for the same reason.

**Revert on failure.** A ticket that never passed its gate must not leave half-written files for
the next ticket to compile against.

**Integration failures get repaired.** A per-ticket typecheck structurally cannot see that a
route violates a framework contract, or that an import the bundler can't resolve typechecks
fine. Those surface at integration, long after the owning ticket committed — so the failure is
blamed back by file path (including through generated `.next/types/…` paths) and that ticket is
reopened with the failure as context.

---

## Two factories

Both apply the same rule to different deliverables. Pick by what the ticket produces.

| | `build_architecture.py` | `run_factory.py` |
|---|---|---|
| **Input** | a technical architecture | a business/rollout report |
| **Unit** | a changeset — several files in a repo | one artifact file |
| **Gate** | the repo's own toolchain | deterministic checks over the file |
| **Output** | commits on a branch | files in `drops/outbox/factory/` |

The document factory (`artifact_kinds.py`, `ticket_contracts.py`) handles markdown runbooks,
JSON contracts and YAML config with checks appropriate to them: required sections, parseable
code fences, required keys, minimum collection sizes, no unfilled placeholders. Its contracts
are authored by a model, so four guards keep them from becoming self-grading — authored before
any builder exists, frozen on disk, rewritten before every run, and **rejected unless they fail
against an empty artifact**.

```powershell
python run_factory.py --plan     # intake + routing, zero API calls
python run_factory.py            # + author, validate and freeze contracts
python run_factory.py --build    # + build, gate and deliver
```

---

## Module map

**The factory**

| File | Role |
|---|---|
| `import_architecture.py` | architecture → tickets; verifies the export is complete |
| `changesets.py` | contracts (which files a ticket owns) + the multi-file builder |
| `greenfield.py` | the target repo: scaffold, git, gates, Supabase stack |
| `build_architecture.py` | the driver: layers → build → gate → commit/revert → repair |

**Supporting layers** — each runnable on its own; `python adz_showcase.py` tours them.

| Directory | Role |
|---|---|
| `scout/` | progressive disclosure — pick files by *name* with a cheap model, then read only those. 90–98% token reduction vs a repo dump |
| `prompt_registry.py`, `commands/` | markdown prompt templates, `{{variable}}` interpolation, higher-order prompts |
| `hooks/` | lifecycle hook bus — `PRE_TOOL_USE` blocks destructive commands, `POST_TOOL_USE` feeds results back |
| `sandbox/` | e2b sandboxes, git worktrees, the tool-calling loop |
| `blueprints/` | workflows as data; `tool_shed.py` defers 30 tool schemas behind `discover_tools` (~47× less up-front context) |
| `meta/` | `compile_worker_prompt()` turns a ticket's expertise into a specialist prompt; `AgentExpert` persists lessons per role, fed **the gate's verdict** and never the agent's claim |
| `tools/` | the agent toolkit: filesystem, git, search, shell, web, tasks |
| `factory_router.py` | routes ticket complexity to a model tier and retry budget |

---

## Design rules

Load-bearing. Each exists because its absence caused a real failure.

- **A gate that can't run reports `SKIPPED` with a reason** — never folded into a pass. A
  silently-skipped gate prints "PASS" while measuring nothing.
- **Verify a gate still bites.** After changing gate config, break something on purpose and
  confirm it fails. Excluding `.next` from the base tsconfig once made `next build` go green
  while a route still violated its contract — the gate stopped measuring rather than started
  passing.
- **Writes are bounded before they touch disk.** `write_files` refuses anything outside `src/`,
  `tests/`, `supabase/`. `package.json` and `tsconfig.json` define what the gate *means*, so a
  changeset editing them could change the verdict instead of satisfying it.
- **Git writes are guarded.** `_assert_own_repo()` runs before every commit, branch and clean.
  A broken `.git` in the target makes git walk *up* and operate on the factory's own repo.
- **Never stop someone else's containers.** `allocate_supabase_ports()` asks Docker what's
  published and picks a free block; `supabase_stop()` always passes `--project-id`.
- **Dependencies use caret ranges**; reproducibility comes from the committed lockfile. Don't
  pin back to exact versions — the owner keeps them current for security.

---

## Limitations

Worth knowing before trusting it with something real.

- **Test coverage is thin.** The builder writes tests when it judges them useful, not because
  the contract demands them. Requiring a `tests/*.test.ts` path per ticket is a one-line
  contract change, not yet made.
- **The database is only reset, never exercised.** `supabase db reset` proves the migration
  *applies*; nothing runs a query against the result.
- **No PR handoff.** `hooks/sandbox_workflow.create_pull_request` exists and is unwired; runs
  stop at a local branch.
- **Greenfield only.** Building changesets into an existing codebase needs the `scout/`
  disclosure layer wired into the changeset path.
- **The scaffold is Next + Supabase + TypeScript.** Another stack means another `greenfield.py`
  scaffold and gate set; the rest of the pipeline is stack-agnostic.
- **`supabase db reset` is the least deterministic step** — it has hit a transient 502 on
  container restart after the migration already applied.
