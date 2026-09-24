"""
tools — the agent toolkit: filesystem, git, search, shell, web, tasks.

declarations.py uses relative imports (`from .file_tools import ...`),
so this package marker has to exist for `import tools.declarations` to
resolve at all.

Exports are lazy. Importing FileSystemTools at package import time would
make `import tools` print its initialisation banner and touch the
filesystem as a side effect, which is unwanted when something only wants
a schema list.
"""

__all__ = ["AgentToolsManager", "FileSystemTools", "TaskRegistry"]


def __getattr__(name: str):
    if name == "AgentToolsManager":
        from .declarations import AgentToolsManager
        return AgentToolsManager
    if name == "FileSystemTools":
        from .file_tools import FileSystemTools
        return FileSystemTools
    if name == "TaskRegistry":
        from .task_tools import TaskRegistry
        return TaskRegistry
    raise AttributeError(f"module 'tools' has no attribute {name!r}")
