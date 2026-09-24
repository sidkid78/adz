# notes/ — early plans, not a spec

These are the design sketches ADZ started from: blueprints, guides and the
`code-examples/` scripts. They were the plan, never verified to work as
written, and the repo has since built past most of them. Several contradict
rules the working code now enforces — the example dashboard shows sample
dollar figures when it has no data, the PR synthesis coordinator commits a
merge without running the gate it describes, and the examples target
Anthropic/OpenAI while the factory is Gemini throughout.

Read them for intent. For behaviour, read the code and `CLAUDE.md`.

| Sketch | Where it actually lives now |
|---|---|
| `adz_watcher_daemon.py`, `adw_runner.py` | `architecture_watcher.py`, `build_architecture.py` |
| `unified_factory_dashboard.py` | `factory_telemetry.py`, `dashboard.html`, `cost.py` |
| `factory_router.py` (GitHub webhook → sandbox → PR) | `hooks/webhook_server.py`, `hooks/ci_repair_workflow.py` |
| `agent-harness-core.py` | `agent_harness.py`, `prompt_registry.py`, `hooks/hook_bus.py` |
| `meta-prompt-cl.py` | `meta/meta_prompt_agent.py` |
| `git_worktree_isolation_hook.py` | `sandbox/git_worktree.py` (e2b only); parallel layer builds are planned |
| `pr_synthesis_coordinator.py` | not needed — each file has one owning ticket, so branches can't conflict |
| `auto_compact_context_hook.py` | not needed yet — `scout/` + a fresh session per ticket keep context small |
| Downloads organizer (`downloads-*`) | a separate personal use of the `dropzone_watcher.py` pattern |
