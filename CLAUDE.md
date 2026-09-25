# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

ADZ ("Autonomous Developer Zone") is a **demonstration/reference implementation of an AI
software factory** built on Gemini. It is not a library or a deployed product — it is a set of
composable, individually-runnable Python modules, each illustrating one pillar of an autonomous
coding pipeline (see README.md for the 7-pillar breakdown). Most files carry long explanatory
docstrings that are part of the deliverable; preserve that teaching tone when editing them.

## Commands

```powershell
# Master demo — interactive menu, all phases, or one phase
python adz_showcase.py
python adz_showcase.py --all
python adz_showcase.py --phase 5

# The software factory: orch2 architecture in, compiling repo out
python import_architecture.py --list <orch2>/backend/workflow_logs/executions
python import_architecture.py <orch2-execution>.json --name pm-mcp-server
python build_architecture.py specs/pm-mcp-server.architecture.json --plan    # no API calls
python build_architecture.py specs/pm-mcp-server.architecture.json --build
python build_architecture.py specs/... --build --no-supabase   # skip the DB step
python greenfield.py pm-mcp-server --force     # scaffold the target repo alone

# Hands-off: watch orch2 and build finished architectures as they land
python architecture_watcher.py                 # watch + build
python architecture_watcher.py --plan-only     # import + report, never build
python architecture_watcher.py --once <execution.json>   # one file, then exit

# Observability
python factory_telemetry.py --serve            # -> http://localhost:8777/dashboard.html
python factory_telemetry.py                    # tail the latest run's events
python reachability.py factory_workspace/<name>   # orphan check on its own
python runtime_proof.py factory_workspace/<name> / /dashboard   # boot the build, assert 200s
python visual_review.py factory_workspace/<name>  # vision review of the proof screenshot
python clean_install.py factory_workspace/<name> [--no-build]   # clone HEAD, npm ci, build

# The document factory (kept; the right tool when the deliverable is prose)
python run_factory.py --plan          # intake + routing for everything in specs/, zero API calls
python run_factory.py                 # + author/validate/freeze per-ticket contracts
python run_factory.py --build         # + build, gate, and deliver to drops/outbox/factory/
python run_factory.py specs/nfo.json --ticket 4 --build   # one file, one ticket
python spec_intake.py                 # what the factory sees in specs/, no API calls
python ticket_contracts.py            # smoke-test the vacuity gates, no API key

# The deterministic quality gate (ruff + mypy + pytest on target_code.py)
bash check.sh

# Tests
uv run pytest                      # whole repo
uv run pytest test_target_code.py -v

# Lint / types (same commands CI runs)
uv run ruff check .
uv run mypy .

# Long-running services
uv run uvicorn webhook_server:app --reload   # must be run from hooks/ (see Imports below)
python dropzone_watcher.py                   # watches drops/inbox/* per drops.yaml
```

Dependencies are managed with **uv** (`uv sync`); Python 3.12. The runtime-proof screenshot
uses the factory's own Playwright, which needs `python -m playwright install chromium` once.

## Environment

`.env.local` at the repo root (gitignored):

```env
GEMINI_API_KEY="..."
E2B_API_KEY="..."   # only needed for live cloud sandboxes
```

Most modules read `os.environ["GEMINI_API_KEY"]` directly and will `KeyError` without it.
`adz_showcase.py` is the only entry point that calls `load_dotenv()` itself.

## Architecture

**Two factories live here.** Both apply the same rule — code decides pass/fail — to different
deliverables. Pick by what the ticket produces.

**1. The software factory** (`build_architecture.py`) — the primary one. Input is an orch2
*technical architecture*; output is a real repo that compiles and passes tests.

```text
orch2 execution  ->  import_architecture.py   preserve dependencies, output_format, complexity;
                                              FAIL LOUDLY on a short export
                 ->  build_order()            Kahn layers; nothing builds before what it imports
                 ->  changesets.py            derive owned files from the architecture document
                 ->  greenfield.py            scaffold a real git repo, npm install once
                 ->  dependencies.py          install the npm packages the architecture's
                                              TS/JS fences import (registry-verified)
                 ->  api_surface.py           excerpt the INSTALLED .d.ts for the builder
                 ->  ChangesetAgent           write all of a ticket's files in one turn
                 ->  per-ticket gate          tsc (SOURCE only) + vitest; seconds, so it
                                              runs on every repair attempt
                 ->  commit, or revert and escalate
                 ->  integration gate         typecheck, vitest, `next build`,
                                              `next typegen` + route contracts,
                                              `deno check` (edge functions),
                                              reachability, runtime proof,
                                              visual review, clean install,
                                              `supabase db reset`; minutes, once
                 ->  blame + repair           map the failure to the ticket owning the
                                              file, reopen it, re-run the gate
```

`architecture_watcher.py` closes the loop in front of this: it watches orch2's executions
directory and runs the pipeline when a finished architecture lands. Every stage emits events to
`factory_runs/<run_id>/events.jsonl` (`factory_telemetry.py`), which `dashboard.html` polls.

**Two tsconfigs, on purpose.** Next generates route-contract types under `.next/types` and adds
them to `include` in `tsconfig.json`; the `check:routes` step typechecks through that config.
The per-ticket gate needs the opposite — a check that means the same thing whether or not a
build has run — so it uses `tsconfig.check.json`, which excludes `.next`. Excluding `.next` from
the *base* config instead silently disables the contract check: the gate stops measuring rather
than starts passing. Verify any change here by breaking a route on purpose (make `params` sync)
and confirming the gate fails.

**Dependencies use caret ranges; reproducibility comes from the lockfile.** Don't pin them back
to exact versions — the owner keeps them current for security (Next 15 carries known CVEs), and
`npm ci` against a committed `package-lock.json` already makes a run reproducible. If a version
change looks like drift, check whether it was deliberate before reverting it.

**Next 16 specifics** (all three cost a debugging cycle):
- Turbopack is the default bundler. A `webpack` key in `next.config.mjs` makes the build refuse
  to start, so the `extensionAlias` fix that worked on 15 is gone.
- Relative imports must be **extensionless** (`./lib/supabase`, not `./lib/supabase.js`) —
  `moduleResolution: "bundler"` expects that, and Turbopack won't resolve `.js`→`.ts`.
- `next build` **no longer typechecks** the generated route validator. `next typegen` +
  `tsc -p tsconfig.json` are separate integration steps; without them the route contracts are
  generated and never checked.

**npm scripts must not chain with `&&`.** npm runs scripts through the platform shell; on Windows
that's PowerShell 5.1, where `&&` is a parser error. Split into separate scripts and steps.

**`write_files` refuses anything outside `src/`, `tests/`, `supabase/`** *before* touching disk.
`package.json` and `tsconfig.json` define what the gate means, so a changeset that edits them
could change the verdict instead of satisfying it.

**The gates verify code, not that the project is usable.** Four defects shipped through a fully
green build for this reason: a missing `tsconfig.check.json`, twelve modules nothing imported,
Tailwind never installed, and no `dev` script. Every one typechecked, built and tested.

- **Reachability** (`reachability.py`) closes part of it. `pm-mcp-server` passed every gate and
  served zero tools: seven tool modules and five prompt modules registered by import side effect
  and nothing imported them. "Is this module reachable from an entry point" is a question no
  compiler asks. It is the blind spot in first-writer-wins ownership — the DAG guarantees a
  ticket's dependencies *exist*, never that its output is *consumed*.
  Repairs game it. After tests stopped counting as entry points, a repair wrote
  `import * as X; void X` into a page, bare-imported pure modules from a route, and
  `await import()`-ed them in `instrumentation.ts` — 286k tokens to make modules *look*
  consumed. An import now counts only if a binding is really referenced, or (bare/discarded
  dynamic import) the target has module-scope effects. When changing the check, re-run it over
  every `factory_workspace/*` build: pm-mcp-server's self-registering tools must stay reachable.
- **Toolchain is the scaffold's job, never a ticket's.** `write_files` refuses `package.json`, so
  a ticket *cannot* add Tailwind, a run script or a tsconfig. Anything in that class belongs in
  `greenfield.py`. When generated code assumes a tool (the frontend expert role is literally
  `frontend_development_tailwind_next_js`), the scaffold must supply it.
- **Fallback paths must land on real entry points.** `FORMAT_FALLBACK_PATHS` in `changesets.py`
  once wrote Next components to `src/app/<ticket>.tsx` (not a route — App Router needs
  `page.tsx` in a directory) and scripts to `src/<ticket>.ts` (nothing imports them). Both
  typecheck; neither can run.
- **Static gates can't see a running app.** `runtime_proof.py` boots the existing production
  build and asserts 200s with a non-trivial body (a repo whose `/` was 404 passed every static
  gate twice). `clean_install.py` clones committed HEAD and runs `npm ci` + build, catching
  anything that only works because of accumulated workspace state (lockfile drift, gitignored
  files). Teardown uses `taskkill /T /F` on Windows — an orphaned dev server holding a port is
  how the next run fails mysteriously.
- **Signed out, a login-gated app proves only its login page.** `signed_in_proof.py` (called
  from `runtime_proof.py`) creates a throwaway user, seeds one row per table from PostgREST's
  OpenAPI description, requests every static page with the `@supabase/ssr` session cookie, and
  names the page's source file plus the server log on failure. It needs data, not just a
  session: `rows.map(clientFn)` over an empty list never calls `clientFn`. It also needs the
  Supabase env **at build time** — Next inlines `NEXT_PUBLIC_*` — so `supabase_start()` writes
  `.env.local` (merged, gitignored) and `supabase db reset` runs *before* `next build`. Verified
  by restoring the loogic dashboard's server-calls-client-function crash: the check fails on
  `src/app/(dashboard)/page.tsx` with the real error.
- **`visual_review.py` is the one model-checked step, and it is asymmetric.** A vision model reads
  the runtime-proof screenshot; a CRITICAL finding can fail an otherwise-green build, but its own
  "PASS" is discarded and recomputed from its findings, so it can never rescue a failed step.
  Keep it that way — letting a model opinion overturn an exit code breaks the central rule.
- **Some compiling code is still refused.** `changesets.py` rejects a changeset that casts to
  `never` (`as unknown as never` silences the compiler rather than satisfying it) or leaves the
  Supabase `Database` type incomplete. Add a check there when a new "compiles but defeats the
  type system" pattern appears, rather than trusting `tsc`.

**Packages come from the architecture, APIs from the installed `.d.ts`.** Tickets can't edit
`package.json`, so `dependencies.py` extracts imports from the architecture's TS/JS fences only
(SQL `FROM "x"` and path aliases otherwise leak in) and verifies each name against the npm
registry before installing; peer conflicts are resolved with a scoped `overrides` block.
`api_surface.py` then hands the builder excerpts of the *installed* type declarations, because
architectures are written from training data (Inngest v3 against an installed v4). Never fix
that class of failure by patching one library's name upstream — it doesn't scale.

**Cost is recorded in tokens; dollars only when priced.** `cost.py`'s `Ledger` records exact
token counts (including billed thought tokens) and derives dollars only for models present in
`PRICES`. Don't add an estimated price — a stale table produces confident wrong numbers.

**Infrastructure failures are not code failures.** `is_infra_failure()` in `greenfield.py`
classifies a gate transcript as environment vs. code (refused sockets, 5xx, port conflicts,
container errors, timeouts), and the repair loop refuses to blame a ticket for one. A transient
`supabase db reset` failure once blamed four tickets and queued rewrites of correct code, purely
because the CLI's output mentioned their file paths — worse than not checking, since it spends
tokens to make good code different. `Step.retries` exists only for genuinely flaky steps and only
fires when the failure classifies as infra; never retry a real gate failure into silence.

**Verify a gate still bites after changing it.** Break something on purpose and confirm the
failure. Excluding `.next` from the base tsconfig once made `next build` go green while a route
still violated its contract — the gate stopped measuring rather than started passing.

**Supabase ports are allocated, never assumed.** `allocate_supabase_ports()` asks Docker which
ports are published and picks a free 100-block. Developers commonly have several Supabase
projects up (this machine had 33 containers across 54321–54527). When `supabase start` reports a
port clash it suggests stopping the other project — never do that, it is someone else's work.
`supabase_stop()` always passes `--project-id`.

**2. The document factory** (`run_factory.py`) — for tickets whose deliverable is prose or
config, graded by `artifact_kinds.py` checks. Built for an orch2 *business rollout* report
(`specs/nfo.json`). Don't reach for it when the deliverable is code.

Module map:

- `scout/` — progressive disclosure. `scout()` sees **file names only** (cheap model), `disclose()`
  then reads just those files. `context_rules.py` injects path-scoped rules. Never replace this
  with a full-repo dump; the token economy is the point.
- `prompt_registry.py` + `commands/*.md` — markdown templates with YAML front matter,
  `{{variable}}` interpolation, and higher-order prompts (a `CompiledPrompt` can be passed as a
  variable value to nest one template inside another). `system_instruction` in front matter travels
  with the compiled prompt.
- `hooks/` — `hook_bus.py` defines `HookEvent` (SETUP, PRE/POST_TOOL_USE, NOTIFICATION, STOP,
  SUBAGENT_STOP). Hooks are plain `HookContext -> HookDecision | None` functions. `PRE_TOOL_USE`
  blocks destructive commands; `POST_TOOL_USE` returns `feedback` that is spliced into the model's
  next turn (the self-repair loop). The bus is model-agnostic — keep Gemini specifics out of it.
- `sandbox/` — e2b sandbox pool, git worktrees, and `SandboxedHookedAgent` (the tool loop).
- `blueprints/blueprints.py` — workflows as data: a list of `Step`s that are either
  `deterministic` (shell command, exit-code gated) or `agent`.
- `meta/` — **wired into the factory, not a side demo.** `meta_prompt_agent.compile_worker_prompt()`
  turns a ticket's `source_expertise` into a specialist system prompt (cached to
  `meta/worker_prompts/<role>.md`, since it's a pro-tier call and roles repeat across tickets).
  `AgentExpert` persists per-role run counts and distilled lessons to `meta/experts/<role>.yaml`
  and folds them into later prompts. `artifact_agent.py` connects both: expertise -> compiled
  specialist -> expert memory -> build -> **gate verdict -> `record_outcome()`**. That last arrow
  is the one `agent_expert.py`'s own `__main__` flagged as missing ("success=True is hardcoded
  here for demo purposes... in a real pipeline this MUST come from an actual deterministic
  check"). Never pass an agent's self-assessment to `record_outcome()`.
- `factory_router.py` — `ROUTES` / `COMPLEXITY_ROUTES` map ticket class to model tier, sandbox
  use, and parallel attempt count. Routing changes *which* model and *how many* attempts, never
  what passing means.
- `spec_intake.py` — the front door. Deterministic parsing first (orchestrator reports, ticket
  lists), model decomposition only for unstructured prose. Empty files are skipped, not sent to a
  model to hallucinate over.
- `artifact_kinds.py` / `ticket_contracts.py` / `artifact_agent.py` — kinds+checks, contracts, and
  the specialist builder. See the conventions below before changing any of them.
- `architecture_watcher.py` — the front door for hands-off runs. Seeds existing executions as
  *seen* on startup rather than backfilling them (orch2 has 50; "catching up" would launch 50
  builds). Waits for size stability **and** a complete export before accepting a file, skips
  architectures whose tickets yield no file contracts, caps plan size with `--max-tickets`,
  serialises builds, and logs every decision to `factory_runs/watcher.jsonl`.
- `factory_telemetry.py` + `dashboard.html` — one JSON object per event, `fsync`'d per line so a
  crashed run still leaves a readable record. Telemetry must never fail a build: every emit is
  wrapped and a dropped event is the worst case. `RunLog` starts **before** the scaffold, because
  a scaffold or baseline failure is exactly the case you most want recorded.
- `reachability.py` — walks the import graph from real framework entry points and fails on files
  the factory built that nothing imports. See the conventions below.

## Conventions that matter here

**Code decides pass/fail, never the agent.** This is the repo's central rule. An agent's own
claim of success is never trusted: pytest exit codes are ground truth, and in `blueprints.py` a
following `deterministic` step's exit code is what gets passed to `expert.record_outcome()`.
Don't introduce agent self-assessment as a success signal.

**Every ticket is graded against its own contract.** `ticket_contracts.py` picks an artifact
*kind* per ticket, authors checks specific to that ticket, and freezes them to
`contracts/<slug>.json`. This exists because the gate used to be two module-level constants
(`check.sh` + `test_target_code.py`, the `add()` suite) applied to every ticket, so a build could
"PASS" against a gate with nothing to do with the ticket.

Five rules keep a model-authored gate from becoming self-grading:

- authored by the reasoning tier **before** any build agent exists;
- frozen on disk, reused byte-for-byte across retries and all parallel racers;
- the executor rewrites the test file from the frozen contract before *every* run and never reads
  checks from the workspace, so an agent editing its own gate accomplishes nothing;
- **non-vacuity** — `validate_contract()` runs the gate against an *empty* artifact and rejects it
  unless it fails. It checks this twice: the full gate must fail, *and* the ticket-specific checks
  must not all pass on their own. Without the second test the kind's mandatory checks mask a
  discretionary set that asserts nothing;
- **specificity** — a contract carrying only the kind's mandatory checks is rejected
  (`MIN_SPECIFIC_CHECKS`).

**Artifact kinds, not just Python.** `artifact_kinds.py` holds `KINDS` (python_module,
markdown_document, json_document, yaml_config) and `CHECKS`, a registry of deterministic
functions over a file. Checks are *data* (`{"type": ..., ...params}`), matching `blueprints.py`.
Adding a kind means adding to those two dicts; nothing else needs to know. No check may ask a
model for an opinion — that is the whole point.

**Structure is disclosed to the builder; spot-checks are not.**
`TicketContract.requirements_brief()` tells the builder the required sections, keys and minimum
counts up front, and withholds `regex_present`/`regex_absent`. Rationale in its docstring: hiding
structure just burns retries rediscovering key names, while a disclosed regex can be satisfied by
pasting the string without doing the work. The integrity guarantee is that the builder did not
author the gate and cannot edit it — not that it never saw it.

**Sandbox only what executes.** `needs_sandbox()` returns true only for `python_module`, because a
sandbox contains code the gate *runs*. Documents and configs are parsed, never executed, so
sandboxing them costs a container per attempt and buys nothing.

**Refusing a ticket is a valid outcome, but should now be rare.** `author_contract()` sets
`buildable: false` only when the deliverable is an action in the world (placing calls, signing
contracts, clicking through a vendor console). Producing the spec, runbook, mapping or config that
*describes* such work is buildable — prefer that.

**Gemini API style.** Everything uses `google-genai` with
`client.interactions.create(model=..., system_instruction=..., input=...)` and reads
`interaction.output_text`. `client.chats.create(...)` is used only where a *single session with
memory of its prior attempt* is required (`software_factory_example.py`, showcase phase 5) — that
session continuity is load-bearing for the repair loop, not incidental. `ChangesetAgent` and
`artifact_agent.py` get the same continuity from the Interactions API by threading
`previous_interaction_id` across a ticket's attempts, which also keeps its context cached.

**Model tiers.** Model ids are inline string constants near the top of each module (scout /
flash-lite for cheap filtering, pro for planning, flash for building). Keep the cheap-scout,
expensive-plan split when adding routes.

**Imports are inconsistent by design of history — check before adding one.** `agent_harness.py`
and the showcase use package-qualified imports (`from hooks.hook_bus import ...`).
`hooks/webhook_server.py` uses flat sibling imports (`from sandbox_workflow import ...`) and must
therefore be launched from inside `hooks/`. `blueprints/blueprints.py` uses `try/except ImportError`
fallbacks to work both ways. Match the file you are editing rather than normalizing across files.

**Dependencies.** `e2b`, `ruff`, `mypy`, and `pytest` are installed in `.venv` but are **not**
declared in `pyproject.toml`. If you touch dependency handling, add them rather than assuming the
manifest is complete.

**`notes/` is early plans, not a spec.** The blueprints and `notes/code-examples/` were never
verified and several contradict rules the code now enforces. Read them for intent only; don't
port them into the factory as written. `notes/README.md` maps each to what replaced it.

**Legacy duplicates.** Hyphenated files (`factory-router.py`, `adz-watcher.py`) are older,
non-importable versions of the underscored modules (`factory_router.py`, `dropzone_watcher.py`).
Edit the underscored ones.

**`target_code.py` is disposable.** It is the agent's scratch output for `software_factory_example.py`,
which overwrites it. `test_target_code.py` + `check.sh` are the contract it must satisfy — don't edit
the tests to make a run pass. Note it does **not** currently pass its own `check.sh` (ruff TRY004);
that is pre-existing. `factory_router.py` no longer writes to it — builds run in a temp workspace.

**Client material never gets committed.** The repo is public and the factory runs real client
engagements. `drops/inbox/` (what a client sent) and `drops/outbox/` (what was delivered) are
gitignored, and a local `.git/hooks/pre-commit` refuses any staged path under them, `git add -f`
included. Don't bypass it with `--no-verify`. The same applies to imported architectures:
`specs/*.architecture.json` is ignored and the hook refuses a newly added one; the four demo specs
already tracked stay tracked.

**Code vs. document is decided before anything builds.** `changesets.deliverable_kind()` calls an
architecture a document job when no ticket names a single file — every path would come from
`fallback_path()`. `architecture_watcher.py` routes those to `run_factory.py` (on the raw orch2
execution, which is the format its intake reads), and `build_architecture.py --build` refuses them
unless given `--code-anyway`. The rule is deliberately conservative: a mixed plan stays code.

## Config files

- `drops.yaml` — drop-zone definitions (inbox/outbox dir, model, prompt, optional validators) for
  `dropzone_watcher.py`.
- `factory-validation.yml` — the GitHub Actions gate. Runs only on branches starting with `agent/`
  or `factory/`, and POSTs to the router's `/webhook/ci-failure` on failure, which is what triggers
  `hooks/ci_repair_workflow.py`.
- `specs/` — input specs and `nfo.json`, the orchestrator-report format consumed by
  `ingest_orchestrator_report.py`.
- `contracts/` — frozen per-ticket acceptance contracts, one JSON per ticket slug. These are a
  record of what a ticket was graded against, so they belong in version control. Delete one (or
  pass `--force-contracts`) to have it re-authored.
