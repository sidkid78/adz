"""
tool_shed.py — The Tool Shed Pattern

Instead of handing an agent hundreds of tool schemas up front (which
blows out the context window before it's done anything), the agent
starts with exactly ONE tool: discover_tools. It searches the shed by
keyword, gets back a small set of matching schemas, and only THOSE get
added to its tools list for the next turn.

Worth noticing: this is the same shape as the tool_search pattern in
Claude's own environment — a long list of MCP tools deferred behind a
search step for exactly this context-bloat reason. The Tool Shed
pattern isn't a novel idea being introduced here, it's the same
architecture you're reading this response through.
"""

from dataclasses import dataclass
from typing import Callable

DISCOVER_TOOLS_META_TOOL = {
    "type": "function",
    "name": "discover_tools",
    "description": "Search the tool shed for tools relevant to a task. Call this BEFORE attempting anything you don't already have a tool for.",
    "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Keywords describing what you need to do"}},
        "required": ["query"],
    },
}


@dataclass
class ShedTool:
    schema: dict
    keywords: list[str]
    executor: Callable[[dict], dict]


class ToolShed:
    """A registry of potentially hundreds of tools. Nothing here is sent
    to the model until discover_tools surfaces it."""

    def __init__(self):
        self._tools: dict[str, ShedTool] = {}

    def register(self, name: str, schema: dict, keywords: list[str], executor: Callable[[dict], dict]) -> None:
        self._tools[name] = ShedTool(schema=schema, keywords=keywords, executor=executor)

    def discover(self, query: str, max_results: int = 5) -> list[dict]:
        """Cheap keyword overlap for this demo — swap for an embedding
        search or an LLM call at real scale, the interface stays the same."""
        query_words = {w.lower() for w in query.split()}
        scored = []
        for name, tool in self._tools.items():
            overlap = len(query_words & {w.lower() for w in tool.keywords})
            if overlap > 0:
                scored.append((overlap, name))
        scored.sort(reverse=True)
        return [self._tools[name].schema for _, name in scored[:max_results]]

    def execute(self, name: str, args: dict) -> dict:
        if name not in self._tools:
            return {"error": f"Tool '{name}' not found — call discover_tools first"}
        return self._tools[name].executor(args)

# ---------------------------------------------------------------------
# Loading the real toolkit into the shed
# ---------------------------------------------------------------------
# The shed above is the mechanism; this is the part that fills it. The
# 30 tools in tools/declarations.py are exactly the case the pattern is
# for: handed over in full they are ~15k tokens of schema before the
# agent has read a single line of a spec.

# Curated keywords, added on top of the ones derived from each tool's
# own name and description. Derived words alone miss the vocabulary an
# agent actually searches with — it asks for "search", not "grep_files".
KEYWORD_HINTS: dict[str, list[str]] = {
    "read_file": ["read", "open", "view", "inspect", "contents", "source"],
    "read_multiple_files": ["read", "batch", "multiple", "several", "files"],
    "create_file": ["write", "create", "new", "save", "author", "emit"],
    "update_file": ["write", "update", "modify", "change", "replace"],
    "edit_file": ["edit", "modify", "patch", "change", "replace", "fix"],
    "delete_file": ["delete", "remove", "rm", "destroy"],
    "move_file": ["move", "rename", "relocate"],
    "copy_file": ["copy", "duplicate", "clone"],
    "create_directory": ["directory", "folder", "mkdir", "create"],
    "list_directory": ["list", "directory", "folder", "ls", "browse", "contents"],
    "create_project_structure": ["scaffold", "project", "structure", "bootstrap", "skeleton"],
    "get_project_tree": ["tree", "structure", "layout", "overview", "project", "explore"],
    "get_file_info": ["info", "metadata", "size", "lines", "stat", "exists"],
    "grep_files": ["search", "grep", "find", "pattern", "regex", "lookup", "locate"],
    "glob_search": ["glob", "search", "find", "pattern", "filename", "locate", "wildcard"],
    "find_symbol": ["symbol", "function", "class", "definition", "search", "find", "where"],
    "diff_files": ["diff", "compare", "difference", "changes"],
    "bash": ["shell", "bash", "command", "run", "execute", "terminal", "cli"],
    "run_tests": ["test", "tests", "pytest", "verify", "check", "suite"],
    "git_operations": ["git", "commit", "branch", "checkout", "push", "status"],
    "git_diff": ["git", "diff", "changes", "staged", "unstaged"],
    "git_log": ["git", "log", "history", "commits"],
    "git_show": ["git", "show", "commit", "inspect"],
    "web_fetch": ["web", "fetch", "url", "http", "download", "docs", "api", "reference"],
    "execute_batch": ["batch", "parallel", "bulk", "many", "multiple"],
    "task_create": ["task", "todo", "plan", "track", "create", "register"],
    "task_update": ["task", "status", "update", "complete", "progress"],
    "task_get": ["task", "status", "get", "dependencies", "blocked"],
    "task_list": ["task", "list", "todo", "overview", "progress"],
    "spawn_agent": ["agent", "spawn", "delegate", "specialist", "subagent", "expert"],
}

_STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "for", "in", "on", "with", "by",
    "this", "that", "it", "its", "is", "are", "be", "use", "used", "using",
    "you", "your", "if", "not", "from", "at", "as", "can", "will", "only",
}


def _derive_keywords(name: str, description: str) -> list[str]:
    """Tool name plus meaningful description words, so a tool is findable
    by its own vocabulary even without a curated hint."""
    words = {w for w in name.split("_") if w}
    for raw in description.lower().replace("/", " ").replace("-", " ").split():
        word = "".join(ch for ch in raw if ch.isalpha())
        if len(word) > 2 and word not in _STOPWORDS:
            words.add(word)
    return sorted(words)


def load_agent_tools(shed: "ToolShed", manager, only: list[str] | None = None) -> int:
    """Register an AgentToolsManager's declarations into the shed.

    The executor closes over manager.execute_tool, so the shed stays a
    pure index — it never learns how any particular tool works, which is
    what lets a tool be added without touching this file.

    `only` restricts registration to a named subset. That is the seam
    for capability policy: a builder that should not be able to run
    shell commands or delete files simply never has those tools put in
    its shed, rather than being asked not to call them.
    """
    count = 0
    for schema in manager.get_tool_declarations():
        if schema.get("type") != "function":
            continue  # e.g. {"type": "google_search"} — no name to index
        name = schema["name"]
        if only is not None and name not in only:
            continue
        keywords = sorted(
            set(_derive_keywords(name, schema.get("description", "")))
            | set(KEYWORD_HINTS.get(name, []))
        )
        shed.register(
            name=name,
            schema=schema,
            keywords=keywords,
            executor=lambda args, _n=name: manager.execute_tool(_n, args),
        )
        count += 1
    return count


# Capability sets, for `only=`. Naming them here keeps the policy in one
# reviewable place instead of scattered across call sites.
READ_ONLY_TOOLS = [
    "read_file", "read_multiple_files", "list_directory", "get_project_tree",
    "get_file_info", "grep_files", "glob_search", "find_symbol", "diff_files",
    "git_diff", "git_log", "git_show", "web_fetch",
]
WRITE_TOOLS = [
    "create_file", "update_file", "edit_file", "move_file", "copy_file",
    "create_directory", "create_project_structure",
]
DESTRUCTIVE_TOOLS = ["delete_file", "bash", "git_operations", "execute_batch"]
COORDINATION_TOOLS = ["task_create", "task_update", "task_get", "task_list", "spawn_agent"]


if __name__ == "__main__":
    import json
    import tempfile

    from tools.declarations import AgentToolsManager

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        shed = ToolShed()
        registered = load_agent_tools(shed, AgentToolsManager(workspace_root=tmp))

        full_schema_tokens = len(json.dumps(
            AgentToolsManager(workspace_root=tmp).get_tool_declarations()
        )) // 4
        shed_tokens = len(json.dumps(DISCOVER_TOOLS_META_TOOL)) // 4

        print(f"\nRegistered {registered} tools in the shed.")
        print(f"Up-front cost, all tools:  ~{full_schema_tokens:,} tokens")
        print(f"Up-front cost, the shed:   ~{shed_tokens:,} tokens "
              f"({full_schema_tokens // max(shed_tokens, 1)}x less)\n")

        for query in [
            "find where a function is defined",
            "read the spec file",
            "fetch API documentation from a url",
            "run the test suite",
            "delegate this to a specialist",
        ]:
            hits = [s["name"] for s in shed.discover(query)]
            print(f"  discover({query!r})\n    -> {hits}")
