# CLAUDE.md

Guidance for Claude Code when working in this repository.

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

Dependencies are managed with **uv** (`uv sync`); Python 3.12.

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
                 ->  ChangesetAgent           write all of a ticket's files in one turn
                 ->  per-ticket gate          tsc (SOURCE only) + vitest; seconds, so it
                                              runs on every repair attempt
                 ->  commit, or revert and escalate
                 ->  integration gate         typecheck, vitest, `next build`,
                                              `next typegen` + route contracts,
                                              `supabase db reset`; minutes, once
                 ->  blame + repair           map the failure to the ticket owning the
                                              file, reopen it, re-run the gate
```

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
session continuity is load-bearing for the repair loop, not incidental.

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

**Legacy duplicates.** Hyphenated files (`factory-router.py`, `adz-watcher.py`) are older,
non-importable versions of the underscored modules (`factory_router.py`, `dropzone_watcher.py`).
Edit the underscored ones.

**`target_code.py` is disposable.** It is the agent's scratch output for `software_factory_example.py`,
which overwrites it. `test_target_code.py` + `check.sh` are the contract it must satisfy — don't edit
the tests to make a run pass. Note it does **not** currently pass its own `check.sh` (ruff TRY004);
that is pre-existing. `factory_router.py` no longer writes to it — builds run in a temp workspace.

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
