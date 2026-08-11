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