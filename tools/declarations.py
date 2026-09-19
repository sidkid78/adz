"""
Agent Tools Manager - Integration layer for Gemini API
"""

from typing import Dict, List, Any
from datetime import datetime

from .file_tools import FileSystemTools
from .task_tools import TaskRegistry


class AgentToolsManager:
    """
    Manages all tools available to AI agents.
    
    Converts FileSystemTools into Gemini function declarations
    and handles tool execution.
    """
    
    def __init__(self, workspace_root: str = "."):
        self.fs_tools = FileSystemTools(workspace_root)
        self.task_registry = TaskRegistry(workspace_root)
        self.tool_execution_log = []
        print("[OK] Agent Tools Manager initialized")
    
    def get_tool_declarations(self, include_search: bool = False) -> List[Dict[str, Any]]:
        """
        Get Interactions API tool declarations for all tools, including BI tools if loaded.

        Returns a flat list of plain dicts. Each function tool has the shape
        ``{"type": "function", "name": ..., "description": ..., "parameters": <json-schema>}``;
        Google Search is ``{"type": "google_search"}``. These dicts are passed directly
        to ``client.interactions.create(tools=...)``.
        """

        tools: List[Dict[str, Any]] = [
            # File Operations
            {
                "type": "function",
                "name": "create_file",
                "description": "Create a new file with content",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path"},
                        "content": {"type": "string", "description": "File content"},
                        "overwrite": {"type": "boolean", "description": "Overwrite if exists"},
                    },
                    "required": ["path", "content"],
                },
            },
            {
                "type": "function",
                "name": "read_file",
                "description": (
                    "Read file contents. Use start_line and end_line to read only a specific "
                    "section of a large file — this saves tokens and cost. Line numbers are "
                    "1-indexed and inclusive. Call get_file_info first to learn total_lines if "
                    "you are unsure of the range you need."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path"},
                        "start_line": {
                            "type": "integer",
                            "description": "First line to return (1-indexed, inclusive). Omit to start from line 1.",
                        },
                        "end_line": {
                            "type": "integer",
                            "description": "Last line to return (1-indexed, inclusive). Omit to read to end of file.",
                        },
                    },
                    "required": ["path"],
                },
            },
            {
                "type": "function",
                "name": "read_multiple_files",
                "description": (
                    "Read multiple files in a single call. "
                    "More efficient than calling read_file repeatedly when you need several files at once. "
                    "Files that fail are reported individually without aborting the batch."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of file paths relative to workspace root.",
                        },
                    },
                    "required": ["paths"],
                },
            },
            {
                "type": "function",
                "name": "update_file",
                "description": "Update file contents. Use mode='patch' for surgical edits with format: <<<<\\noriginal text\\n====\\nreplacement\\n>>>>",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path"},
                        "content": {"type": "string", "description": "New content or patch content"},
                        "mode": {
                            "type": "string",
                            "description": "Update mode: 'replace' (entire file), 'append', 'prepend', or 'patch' (search-and-replace with <<<<...====...>>>> format)",
                        },
                    },
                    "required": ["path", "content"],
                },
            },
            {
                "type": "function",
                "name": "delete_file",
                "description": "Delete a file",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path"},
                    },
                    "required": ["path"],
                },
            },
            # Directory Operations
            {
                "type": "function",
                "name": "create_directory",
                "description": "Create a directory",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory path"},
                        "parents": {"type": "boolean", "description": "Create parents"},
                    },
                    "required": ["path"],
                },
            },
            {
                "type": "function",
                "name": "list_directory",
                "description": "List directory contents",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory path"},
                        "recursive": {"type": "boolean", "description": "List recursively"},
                        "include_hidden": {"type": "boolean", "description": "Include hidden files"},
                    },
                },
            },
            {
                "type": "function",
                "name": "create_project_structure",
                "description": "Create a complete project structure with multiple directories and files at once",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_name": {"type": "string", "description": "Name of the project"},
                        "structure": {"type": "object", "description": "Project structure with 'directories' and 'files'"},
                    },
                    "required": ["project_name", "structure"],
                },
            },
            {
                "type": "function",
                "name": "get_project_tree",
                "description": "Get a tree view of the project structure",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Root path for tree view"},
                        "max_depth": {"type": "integer", "description": "Maximum depth to traverse"},
                    },
                },
            },
            {
                "type": "function",
                "name": "get_file_info",
                "description": "Get detailed information about a file",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path relative to workspace"},
                    },
                    "required": ["path"],
                },
            },
            {
                "type": "function",
                "name": "bash",
                "description": "Execute bash/shell command. CI=true is set automatically. Use --yes/-y for npm/npx.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Command to execute."},
                        "path": {"type": "string", "description": "Working directory"},
                        "timeout": {"type": "integer", "description": "Timeout in seconds"},
                    },
                    "required": ["command"],
                },
            },
            {
                "type": "function",
                "name": "git_operations",
                "description": (
                    "Perform git write operations: init, status, add, commit, branch, checkout, "
                    "push, pull. Arguments are passed to git directly, so a commit message may "
                    "safely contain quotes or shell metacharacters. To INSPECT a repository use "
                    "git_diff, git_log or git_show instead."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "operation": {"type": "string", "description": "Operation: init, status, add, commit, branch, checkout, push, pull"},
                        "path": {"type": "string", "description": "Repository path"},
                        "message": {"type": "string", "description": "Commit message (for commit)"},
                        "name": {"type": "string", "description": "Branch name (for branch)"},
                        "branch": {"type": "string", "description": "Branch name (for checkout)"},
                        "files": {"type": "string", "description": "Files to add (for add)"},
                    },
                    "required": ["operation"],
                },
            },
            {
                "type": "function",
                "name": "grep_files",
                "description": "Search for pattern in files",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Search pattern (regex)"},
                        "path": {"type": "string", "description": "Search directory"},
                        "file_pattern": {"type": "string", "description": "File glob pattern"},
                        "case_sensitive": {"type": "boolean", "description": "Case sensitive search"},
                    },
                    "required": ["pattern"],
                },
            },
            {
                "type": "function",
                "name": "glob_search",
                "description": "Find files matching glob pattern",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Glob pattern (e.g., **/*.py)"},
                        "path": {"type": "string", "description": "Search directory"},
                    },
                    "required": ["pattern"],
                },
            },
            {
                "type": "function",
                "name": "diff_files",
                "description": (
                    "Show a unified diff. Either compare two files (pass 'other'), or preview a "
                    "pending edit by passing the proposed full text as 'content' before calling "
                    "update_file. Exactly one of 'other' or 'content' is required."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to the original/left-hand file"},
                        "other": {"type": "string", "description": "Path to the file to compare against"},
                        "content": {"type": "string", "description": "Proposed new full file content to diff against the file on disk"},
                        "context_lines": {"type": "integer", "description": "Lines of context around each change (default 3)"},
                    },
                    "required": ["path"],
                },
            },
            {
                "type": "function",
                "name": "edit_file",
                "description": (
                    "PREFERRED way to make a surgical edit. Replaces an exact string in a file. "
                    "old_string is matched byte-for-byte including indentation, and the edit is "
                    "rejected rather than guessed at if it is missing or matches more than once - "
                    "so include enough surrounding lines to make it unique. Returns a diff of the "
                    "change. Use update_file only to rewrite a whole file or append/prepend."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File to edit"},
                        "old_string": {"type": "string", "description": "Exact text to find, including indentation. Must be unique in the file unless replace_all is true."},
                        "new_string": {"type": "string", "description": "Replacement text. May be empty to delete old_string."},
                        "replace_all": {"type": "boolean", "description": "Replace every occurrence instead of requiring exactly one"},
                    },
                    "required": ["path", "old_string", "new_string"],
                },
            },
            {
                "type": "function",
                "name": "move_file",
                "description": "Move or rename a file or directory within the workspace",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string", "description": "Existing path"},
                        "destination": {"type": "string", "description": "New path"},
                        "overwrite": {"type": "boolean", "description": "Replace destination if it exists"},
                    },
                    "required": ["source", "destination"],
                },
            },
            {
                "type": "function",
                "name": "copy_file",
                "description": "Copy a file or directory tree within the workspace",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string", "description": "Path to copy from"},
                        "destination": {"type": "string", "description": "Path to copy to"},
                        "overwrite": {"type": "boolean", "description": "Replace destination if it exists"},
                    },
                    "required": ["source", "destination"],
                },
            },
            {
                "type": "function",
                "name": "git_diff",
                "description": (
                    "Show uncommitted changes in a git repository. Call this before committing to "
                    "confirm what you are about to commit. Use stat_only=true first on a large "
                    "change to see which files are affected without pulling in the whole patch."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Repository path"},
                        "staged": {"type": "boolean", "description": "Diff the index against HEAD instead of the working tree"},
                        "target": {"type": "string", "description": "Diff against a commit or branch, e.g. main or HEAD~1"},
                        "files": {"type": "string", "description": "Limit the diff to these paths (space-separated)"},
                        "stat_only": {"type": "boolean", "description": "Return only the changed-file summary"},
                        "context_lines": {"type": "integer", "description": "Lines of context around each hunk (default 3)"},
                    },
                },
            },
            {
                "type": "function",
                "name": "git_log",
                "description": (
                    "Show recent commit history. Useful for learning the commit message style of a "
                    "repository and its recent activity before making a commit."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Repository path"},
                        "count": {"type": "integer", "description": "Number of commits to return (default 20)"},
                        "oneline": {"type": "boolean", "description": "Compact one-line-per-commit format (default true)"},
                        "files": {"type": "string", "description": "Limit history to these paths"},
                        "author": {"type": "string", "description": "Filter by author substring"},
                    },
                },
            },
            {
                "type": "function",
                "name": "git_show",
                "description": "Show a single commit: its message and the patch it introduced",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ref": {"type": "string", "description": "Commit-ish to show, e.g. HEAD, abc1234, main~2"},
                        "path": {"type": "string", "description": "Repository path"},
                        "stat_only": {"type": "boolean", "description": "Return only the changed-file summary"},
                    },
                },
            },
            {
                "type": "function",
                "name": "find_symbol",
                "description": (
                    "Find where a symbol is DEFINED rather than merely mentioned. Prefer this over "
                    "grep_files when you need to locate the definition of a function, class, type or "
                    "variable. Covers Python, JS/TS, Go, Rust, Java and C#, and skips vendored "
                    "directories such as node_modules and .venv."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Exact symbol name"},
                        "path": {"type": "string", "description": "Directory to search"},
                        "file_pattern": {"type": "string", "description": "Glob filter, e.g. *.py"},
                        "include_references": {"type": "boolean", "description": "Also count non-definition mentions per file"},
                    },
                    "required": ["name"],
                },
            },
            {
                "type": "function",
                "name": "run_tests",
                "description": (
                    "Run the tests for a project and get a parsed summary: pass/fail counts and the "
                    "names of failing tests, with output trimmed to the tail where the failure detail "
                    "is. Prefer this over running pytest through bash, which can return enormous "
                    "output on a failing suite."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Test file or directory to run"},
                        "framework": {"type": "string", "description": "auto (default), pytest, npm, or unittest"},
                        "pattern": {"type": "string", "description": "Only run tests matching this name filter"},
                        "timeout": {"type": "integer", "description": "Seconds before the run is killed (default 300)"},
                    },
                },
            },
            {
                "type": "function",
                "name": "web_fetch",
                "description": (
                    "Fetch a URL and return its readable text. Use for documentation, API references, "
                    "changelogs and raw source files - it returns the actual page, whereas search "
                    "grounding returns only snippets. HTML is stripped to text; JSON is pretty-printed. "
                    "Local and private network addresses are refused."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "http(s) URL to fetch"},
                        "max_chars": {"type": "integer", "description": "Cap on returned text (default 50000)"},
                        "timeout": {"type": "integer", "description": "Seconds to wait for the response (default 20)"},
                        "raw": {"type": "boolean", "description": "Skip HTML-to-text conversion and return the body as received"},
                    },
                    "required": ["url"],
                },
            },
            {
                "type": "function",
                "name": "execute_batch",
                "description": (
                    "Execute several tool operations in one call, in parallel where they are "
                    "independent. tasks is a JSON list of objects like "
                    "{\"operation\": \"read_file\", \"args\": {\"path\": \"a.py\"}}. "
                    "IMPORTANT: tasks run concurrently, so if one task consumes what another "
                    "produces you MUST declare the order by giving the producer an \"id\" and the "
                    "consumer a \"depends_on\" list of ids. Tasks are then run in dependency waves, "
                    "and a task whose dependency failed is reported as skipped rather than run. "
                    "Results come back in submission order, each with an id and a status of ok, "
                    "failed or skipped."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tasks": {
                            "type": "string",
                            "description": (
                                "JSON list of task objects. Each has 'operation' and 'args', plus "
                                "optional 'id' and 'depends_on' (a list of ids that must succeed "
                                "first). Example: [{\"id\": \"mk\", \"operation\": \"create_file\", "
                                "\"args\": {\"path\": \"a.py\", \"content\": \"x = 1\"}}, "
                                "{\"operation\": \"read_file\", \"depends_on\": [\"mk\"], "
                                "\"args\": {\"path\": \"a.py\"}}]"
                            ),
                        },
                        "allowed_tools": {"type": "array", "items": {"type": "string"}, "description": "Restrict which operations this batch may call"},
                        "full_search_path": {"type": "string", "description": "Default path for directory-based operations"},
                        "max_workers": {"type": "integer", "description": "Maximum tasks running concurrently within a wave"},
                    },
                    "required": ["tasks"],
                },
            },
            {
                "type": "function",
                "name": "task_create",
                "description": "Create a new task in the shared task registry.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "Unique task identifier"},
                        "description": {"type": "string", "description": "Description of what this task accomplishes"},
                        "assigned_agent": {"type": "string", "description": "Agent role label"},
                        "depends_on": {"type": "array", "items": {"type": "string"}, "description": "List of task_ids this task depends on."},
                        "status": {"type": "string", "description": "Initial status: pending (default), in_progress, completed, failed, blocked"},
                    },
                    "required": ["task_id", "description", "assigned_agent"],
                },
            },
            {
                "type": "function",
                "name": "task_update",
                "description": "Update a task's status.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "ID of the task to update"},
                        "status": {"type": "string", "description": "New status"},
                        "result_summary": {"type": "string", "description": "Summary of task results"},
                    },
                    "required": ["task_id", "status"],
                },
            },
            {
                "type": "function",
                "name": "task_get",
                "description": "Get details of a specific task including its dependency statuses.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "ID of the task to retrieve"},
                    },
                    "required": ["task_id"],
                },
            },
            {
                "type": "function",
                "name": "task_list",
                "description": "List all tasks in the registry. Optionally filter by status.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "status_filter": {"type": "string", "description": "Filter by status. Omit for all tasks."},
                    },
                },
            },
            {
                "type": "function",
                "name": "spawn_agent",
                "description": (
                    "Dynamically spawn a specialized AI agent for a specific role. "
                    "The PromptArchitect researches the domain via Google Search and generates "
                    "an expert-level system prompt, then a fresh GeminiAgent executes the task."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "agent_role": {"type": "string", "description": "Description of the specialist needed."},
                        "task": {"type": "string", "description": "The concrete task for the spawned agent to execute."},
                        "model": {"type": "string", "description": "Override model. Omit to use PromptArchitect recommendation."},
                    },
                    "required": ["agent_role", "task"],
                },
            },
        ]

        if include_search:
            tools.append({"type": "google_search"})

        if hasattr(self, '_bi_manager'):
            tools.extend(self._bi_manager.get_tool_declarations())

        return tools
    
    def execute_tool(self, tool_name: str, arguments: Dict) -> Any:
        """Execute a tool by name."""
        tool_map = {
            "create_file": self.fs_tools.create_file,
            "read_file": self.fs_tools.read_file,
            "read_multiple_files": self.fs_tools.read_multiple_files,
            "update_file": self.fs_tools.update_file,
            "delete_file": self.fs_tools.delete_file,
            "create_directory": self.fs_tools.create_directory,
            "list_directory": self.fs_tools.list_directory,
            "create_project_structure": self.fs_tools.create_project_structure,
            "get_project_tree": self.fs_tools.get_project_tree,
            "get_file_info": self.fs_tools.get_file_info,
            "bash": self.fs_tools.bash,
            "git_operations": self.fs_tools.git_operations,
            "grep_files": self.fs_tools.grep_files,
            "glob_search": self.fs_tools.glob_search,
            "diff_files": self.fs_tools.diff_files,
            "edit_file": self.fs_tools.edit_file,
            "move_file": self.fs_tools.move_file,
            "copy_file": self.fs_tools.copy_file,
            "git_diff": self.fs_tools.git_diff,
            "git_log": self.fs_tools.git_log,
            "git_show": self.fs_tools.git_show,
            "find_symbol": self.fs_tools.find_symbol,
            "run_tests": self.fs_tools.run_tests,
            "web_fetch": self.fs_tools.web_fetch,
            "execute_batch": self.fs_tools.execute_batch,
            "task_create": self.task_registry.task_create,
            "task_update": self.task_registry.task_update,
            "task_get": self.task_registry.task_get,
            "task_list": self.task_registry.task_list,
            "spawn_agent": self.spawn_agent,
        }
        if self._is_bi_tool(tool_name):
            return self._bi_manager.execute_tool(tool_name, arguments)

        if tool_name not in tool_map:
            return {"success": False, "error": f"Unknown tool: {tool_name}"}
        
        try:
            result = tool_map[tool_name](**arguments)
            self.tool_execution_log.append({
                "tool": tool_name,
                "arguments": arguments,
                "result": result,
                "timestamp": datetime.now().isoformat()
            })
            return result
        except Exception as e:
            return {"success": False, "error": f"Tool execution failed: {str(e)}"}
    
    # def load_bi_tools(self, bi_manager=None):
    #     """Attach Business Intelligence tools to this agent."""
    #     if bi_manager is None:
    #         from .bi_tools import BiToolsManager
    #         bi_manager = BiToolsManager()
    #     self._bi_manager = bi_manager
    #     print("✓ BI tools loaded — 14 analysis tools now available")
    #     return bi_manager

    def _is_bi_tool(self, tool_name: str) -> bool:
        """Live again (was commented out) because execute_tool calls it
        unconditionally. Without it every tool call died on AttributeError
        before reaching its executor. Returns False when no BI manager is
        attached, which is the normal case — bi_tools.py isn't present."""
        return tool_name.startswith("bi_") and hasattr(self, "_bi_manager")

    def get_workspace_path(self) -> str:
        """Get absolute workspace path."""
        return str(self.fs_tools.workspace_root)

    def spawn_agent(self, agent_role: str, task: str, model: str = "auto") -> Dict:
        """Spawn a dynamically specialised agent for one task.

        execute_tool maps "spawn_agent" to this method, so leaving it
        commented out broke the whole dispatch map, not just this tool.

        The "PromptArchitect" step the declaration describes already
        exists in this repo as meta/meta_prompt_agent.compile_worker_prompt
        — it compiles a role description into a full specialist system
        prompt. Reusing it keeps one prompt-compiler in the codebase
        instead of a second, divergent one living in the tool layer.
        """
        import os
        from pathlib import Path

        from google import genai

        try:
            from meta.meta_prompt_agent import compile_worker_prompt
            system_instruction = compile_worker_prompt(
                worker_role=agent_role, required_tools=[], has_hooks=False,
            )
        except Exception as exc:  # noqa: BLE001 - fall back rather than fail the tool call
            system_instruction = (
                f"You are a specialist in: {agent_role}. Produce complete, concrete work. "
                "No pleasantries, no preamble."
            )
            compile_note = f"prompt compilation unavailable ({type(exc).__name__}); used fallback"
        else:
            compile_note = "prompt compiled by meta_prompt_agent"

        # "auto" picks the workhorse tier; an explicit id always wins.
        chosen_model = "gemini-3.6-flash" if model == "auto" else model

        try:
            client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
            interaction = client.interactions.create(
                model=chosen_model, system_instruction=system_instruction, input=task,
            )
            output = interaction.output_text.strip()
        except Exception as exc:  # noqa: BLE001 - tools report failure, they don't raise
            return {"success": False, "error": f"spawned agent failed: {type(exc).__name__}: {exc}"}

        # Keep the run log the original stub intended.
        try:
            log_dir = Path(self.fs_tools.workspace_root) / "runs" / "skill-runs"
            log_dir.mkdir(parents=True, exist_ok=True)
            slug = agent_role[:40].lower().replace(" ", "_").replace("/", "-")
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            (log_dir / f"spawn_agent_{slug}_{stamp}.md").write_text(
                f"# spawn_agent: {agent_role}\n\n"
                f"- model: {chosen_model}\n- prompt: {compile_note}\n\n"
                f"## Task\n\n{task}\n\n## Output\n\n{output}\n",
                encoding="utf-8",
            )
        except OSError:
            pass  # logging is best-effort; never fail the tool over it

        return {
            "success": True,
            "agent_role": agent_role,
            "model": chosen_model,
            "prompt_source": compile_note,
            "output": output,
        }

    # def spawn_agent(self, agent_role: str, task: str, model: str = "auto") -> dict:
    #     """Spawn a dynamically specialized agent using PromptArchitect."""
    #     import json
    #     from datetime import datetime
    #     from pathlib import Path

    #     timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    #     log_lines = []

    #     def log(msg: str):
    #         print(msg)
    #         log_lines.append(msg)

    #     def save_log(result: dict, generated_prompt_text: str = ""):
    #         try:
    #             log_dir = Path(self.fs_tools.workspace_root) / "runs" / "skill-runs"
    #             log_dir.mkdir(parents=True, exist_ok=True)
    #             role_slug = agent_role[:40].lower().replace(" ", "_").replace("/", "-")
    #             log_path = log_dir / f"spawn_agent_{role_slug}_{timestamp}.md"
    #             sections = [
    #                 f"---",
    #                 f"skill: spawn_agent",
    #                 f"agent_role: {agent_role}",
    #                 f"status: {'SUCCESS' if result.get('success') else 'FAILED'}",
    #                 f"generated: {datetime.now().isoformat()}",
    #                 f"---",
    #                 f"",
    #                 f"# spawn_agent Run: {agent_role}",
    #                 f"",
    #                 f"## Task",
    #                 f"```",
    #                 task,
    #                 f"```",
    #                 f"",
    #             ]
    #             if generated_prompt_text:
    #                 sections += [f"## Generated System Prompt", f"```", generated_prompt_text, f"```", f""]
    #             sections += [f"## Execution Log", f"```"] + log_lines + [f"```", f""]
    #             for i, tc in enumerate(result.get("tool_calls", []), 1):
    #                 sections.append(f"### [{i}] {tc['tool']}")
    #                 sections.append(f"**Args:** `{json.dumps(tc.get('arguments', {}))[:200]}`")
    #                 sections.append(f"**Result:** {str(tc.get('result', ''))[:300]}")
    #                 sections.append("")
    #             sections += [f"## Final Response", f"", result.get("response", "(no response)"), f""]
    #             if not result.get("success") and result.get("error"):
    #                 sections += [f"## Error", f"```", result["error"], f"```"]
    #             log_path.write_text("\n".join(sections), encoding="utf-8")
    #             return str(log_path)
    #         except Exception as e:
    #             print(f"  Could not save log: {e}")
    #             return None

    #     try:
    #         from .prompt_architect import PromptArchitect
    #         # from .agent import GeminiAgent

    #         log(f"{'─'*60}")
    #         log(f"spawn_agent: {agent_role}")
    #         log(f"{'─'*60}")

    #         architect = PromptArchitect(use_gemini_cache=False)

    #         log(f"  → Phase 1: PromptArchitect analyzing role...")
    #         analysis = architect.analyze_task(agent_role, thinking_level="LOW")
    #         log(f"  Analysis complete — level: {analysis.recommended_level}, model: {analysis.recommended_target_model}")

    #         log(f"  → Phase 2: Generating expert system prompt...")
    #         generated = architect.generate_prompt(agent_role, analysis, thinking_level="LOW")
    #         log(f"  Prompt generated — {generated.title}")

    #         effective_model = generated.metadata.recommended_model if model == "auto" else model
    #         log(f"  → Phase 3: Spawning specialist on {effective_model}")
    #         log(f"{'─'*60}")

    #         agent = GeminiAgent(
    #             workspace=str(self.fs_tools.workspace_root),
    #             model=effective_model,
    #             thinking_level="LOW",
    #             enable_caching=False,
    #         )
    #         run_result = agent.run(prompt=task, system_instruction=generated.rendered_prompt)

    #         tool_calls = run_result.get("tool_calls", [])
    #         log(f"  Specialist complete — {run_result.get('iterations', 0)} iterations, {len(tool_calls)} tool calls")

    #         response_text = run_result.get("response", "")
    #         result = {
    #             "success": True,
    #             "agent_role": agent_role,
    #             "agent_title": generated.title,
    #             "prompt_level": generated.level,
    #             "model_used": effective_model,
    #             "response": response_text,
    #             "tool_calls": tool_calls,
    #             "tool_call_count": len(tool_calls),
    #             "iterations": run_result.get("iterations", 0),
    #             "web_sources_used": len(analysis.grounding_sources or []),
    #         }
    #         log_path = save_log(result, generated.rendered_prompt)
    #         if log_path:
    #             result["log_file"] = log_path
    #         return result

    #     except Exception as e:
    #         import traceback
    #         log(f"  spawn_agent failed: {e}")
    #         log(traceback.format_exc())
    #         result = {"success": False, "error": str(e), "agent_role": agent_role, "tool_calls": [], "tool_call_count": 0}
    #         save_log(result)
    #         return result
