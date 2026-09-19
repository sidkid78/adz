"""
File System Tools for AI Agents

Provides file operations, command execution, git operations and search
capabilities for AI agents building software.

SCOPE OF THE WORKSPACE GUARANTEE: every path argument is resolved and
confined to the workspace directory, so the file operations here cannot
read or write outside it. That is a path confinement check, NOT a sandbox.
In particular `bash()` runs an arbitrary command with the full privileges
of the host process: its working directory is confined to the workspace,
but the command itself can reach any file, network or device the user can.
Run this against untrusted model output only inside a container or VM.
"""

from pathlib import Path
from typing import Optional, List, Dict, Union, Any
import json
import shutil
import subprocess
import re
import difflib
import threading
import time
from contextlib import ExitStack
import shlex
import socket
import ipaddress
import html
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import os


class FileSystemTools:
    """
    Comprehensive development tools for AI agents.

    Every path argument is confined to the workspace directory (see
    _resolve_path). Note that this is path confinement, not a sandbox:
    bash() executes arbitrary commands with the privileges of the host
    process. See the module docstring.
    """
    
    # Rate limiting constants to prevent excessive API usage
    MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB per file
    MAX_TOTAL_READ_SIZE = 50 * 1024 * 1024  # 50MB total in one operation
    MAX_FILES_TO_SCAN = 500  # Maximum files to scan in directory operations
    MAX_RESULTS_RETURNED = 100  # Maximum items to return in response (prevents huge JSON)
    MAX_BASH_OUTPUT_CHARS = 100_000  # Cap bash output to avoid token limits (approx 25k tokens)
    MAX_DIFF_LINES = 2000  # Cap unified diff output
    MAX_PATTERN_LENGTH = 1000  # Reject absurdly long regexes outright
    # Wall-clock budget for a regex scan. Python's re has no timeout, so a
    # catastrophically backtracking pattern can still hang on a SINGLE file;
    # this bounds the damage across files, which is the common case.
    SCAN_TIME_BUDGET = 30.0
    SKIP_DIRS = frozenset({
        '.git', '__pycache__', 'node_modules', '.venv', 'venv', 'env',
        'build', 'dist', '.mypy_cache', '.pytest_cache', '.ruff_cache',
        '.tox', 'site-packages', '.next', 'target', 'coverage',
    })
    
    def __init__(self, workspace_root: str = "."):
        """Initialize with workspace root directory."""
        self.workspace_root = Path(workspace_root).resolve()
        self.workspace_root.mkdir(exist_ok=True, parents=True)
        self.operations_log = []
        # Guards concurrent writes to the same path from one execute_batch wave.
        self._path_locks: Dict[str, threading.Lock] = {}
        self._path_locks_guard = threading.Lock()
        
        if self.workspace_root:
            print(f"[OK] File System Tools initialized")
            print(f"  Workspace: {self.workspace_root}")
            print(f"  Rate Limits: {self.MAX_FILE_SIZE // 1024 // 1024}MB per file, {self.MAX_FILES_TO_SCAN} files scan, {self.MAX_RESULTS_RETURNED} results max")
    
    def _resolve_path(self, path: str) -> Path:
        """Resolve a path and confine it to the workspace.

        Uses a path-component comparison rather than a string prefix test:
        str.startswith() would accept a sibling directory whose name merely
        begins with the workspace name (workspace-secret vs workspace).
        .resolve() collapses '..' and follows symlinks before the check, so
        neither can be used to step outside.
        """
        full_path = (self.workspace_root / path).resolve()

        if full_path != self.workspace_root and not full_path.is_relative_to(self.workspace_root):
            raise ValueError(f"Path '{path}' escapes workspace")

        return full_path
    
    def _log_operation(self, operation: str, details: Dict):
        """Log an operation for tracking."""
        self.operations_log.append({
            "timestamp": datetime.now().isoformat(),
            "operation": operation,
            **details
        })
    
    # =========================================================================
    # File Operations
    # =========================================================================
    
    def create_file(self, path: str, content: str, overwrite: bool = False) -> Dict:
        """Create a new file with content."""
        try:
            full_path = self._resolve_path(path)
            
            if full_path.exists() and not overwrite:
                return {"success": False, "error": f"File exists: {path}"}
            
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding='utf-8')
            
            self._log_operation("create_file", {"path": path, "size": len(content)})
            
            return {
                "success": True,
                "path": str(full_path),
                "size": len(content)
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def read_file(
        self,
        path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> Dict:
        """Read file contents with optional line range selection.

        Line numbers are 1-indexed and inclusive on both ends.
        Omitting both start_line and end_line returns the entire file.
        Omitting end_line reads from start_line to end of file.
        Omitting start_line reads from the beginning up to end_line.

        Args:
            path:       Path to the file (relative to workspace root).
            start_line: First line to return (1-indexed, inclusive).
            end_line:   Last line to return (1-indexed, inclusive).

        Returns:
            Dict with keys:
                success      - bool
                path         - resolved absolute path
                content      - text of the requested range (or full file)
                size         - character count of returned content
                total_lines  - total lines in the file
                start_line   - actual first line returned (1-indexed)
                end_line     - actual last line returned (1-indexed)
                lines_returned - number of lines in the returned content
        """
        try:
            full_path = self._resolve_path(path)

            if not full_path.exists():
                return {"success": False, "error": f"File not found: {path}"}

            file_size = full_path.stat().st_size
            if file_size > self.MAX_FILE_SIZE:
                return {
                    "success": False,
                    "error": (
                        f"File too large ({file_size // 1024 // 1024}MB). "
                        f"Maximum: {self.MAX_FILE_SIZE // 1024 // 1024}MB. "
                        f"Use start_line/end_line to read a specific section."
                    ),
                }

            full_content = full_path.read_text(encoding="utf-8")
            lines = full_content.splitlines(keepends=True)
            total_lines = len(lines)

            if start_line is not None and start_line < 1:
                return {
                    "success": False,
                    "error": f"start_line must be >= 1 (got {start_line}); line numbers are 1-indexed.",
                }
            if end_line is not None and end_line < 1:
                return {
                    "success": False,
                    "error": f"end_line must be >= 1 (got {end_line}); line numbers are 1-indexed.",
                }

            slice_start = max((start_line or 1) - 1, 0)
            slice_end = min(end_line, total_lines) if end_line is not None else total_lines

            if start_line is not None and start_line > total_lines:
                return {
                    "success": False,
                    "error": f"start_line {start_line} exceeds file length ({total_lines} lines).",
                }

            if end_line is not None and end_line < (start_line or 1):
                return {
                    "success": False,
                    "error": f"end_line ({end_line}) must be >= start_line ({start_line or 1}).",
                }

            selected = lines[slice_start:slice_end]
            content = "".join(selected)
            actual_start = slice_start + 1
            actual_end = slice_start + len(selected)

            return {
                "success": True,
                "path": str(full_path),
                "content": content,
                "size": len(content),
                "total_lines": total_lines,
                "start_line": actual_start,
                "end_line": actual_end,
                "lines_returned": len(selected),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def read_multiple_files(self, paths: List[str]) -> Dict:
        """Read multiple files in a single call, returning all contents.

        Reads files concurrently using a thread pool. Files that fail
        (not found, too large, permission denied) are included in the
        result with their error rather than aborting the whole batch.

        Args:
            paths: List of file paths relative to workspace root.

        Returns:
            Dict with keys:
                success     - True if at least one file was read successfully
                files       - dict mapping path -> read_file result dict
                total_size  - combined character count of successful reads
                read_count  - number of files successfully read
                error_count - number of files that failed
        """
        if not paths:
            return {"success": False, "error": "No paths provided."}

        results: Dict[str, Dict] = {}

        def _read_one(p: str) -> tuple:
            return p, self.read_file(p)

        with ThreadPoolExecutor(max_workers=min(len(paths), 8)) as pool:
            futures = {pool.submit(_read_one, p): p for p in paths}
            for future in as_completed(futures):
                path, result = future.result()
                results[path] = result

        read_count  = sum(1 for r in results.values() if r.get("success"))
        error_count = len(results) - read_count
        total_size  = sum(r.get("size", 0) for r in results.values() if r.get("success"))

        self._log_operation("read_multiple_files", {
            "paths": paths,
            "read_count": read_count,
            "error_count": error_count,
        })

        return {
            "success":    read_count > 0,
            "files":      results,
            "total_size": total_size,
            "read_count": read_count,
            "error_count": error_count,
        }

    def update_file(self, path: str, content: str, mode: str = "replace") -> Dict:
        """Update file (replace, append, prepend, or patch).
        
        Modes:
        - replace: Replace entire file content
        - append: Add content to end of file  
        - prepend: Add content to beginning of file
        - patch: Parse diff format and apply search-and-replace
        
        Patch format:
        <<<<
        original text to find
        ====
        replacement text
        >>>>
        
        Multiple patches can be specified in sequence.
        """
        try:
            full_path = self._resolve_path(path)
            
            if not full_path.exists():
                return {"success": False, "error": f"File not found: {path}"}
            
            existing = full_path.read_text(encoding='utf-8')
            
            if mode == "append":
                new_content = existing + content
            elif mode == "prepend":
                new_content = content + existing
            elif mode == "patch":
                new_content = existing
                patches_applied = 0
                errors = []
                
                patch_pattern = r'<<<<\s*\n(.*?)\n====\s*\n(.*?)\n>>>>'
                patches = re.findall(patch_pattern, content, re.DOTALL)
                
                if not patches:
                    return {
                        "success": False, 
                        "error": "No valid patches found. Use format: <<<<\\noriginal\\n====\\nreplacement\\n>>>>"
                    }
                
                # All-or-nothing: a partially applied patch set leaves the file in
                # a state the caller never asked for and cannot easily reason about,
                # so every patch must match before anything is written to disk.
                for position, (original, replacement) in enumerate(patches, 1):
                    original_normalized = original.strip()

                    if original_normalized in new_content:
                        new_content = new_content.replace(original_normalized, replacement.strip(), 1)
                        patches_applied += 1
                    else:
                        errors.append(f"patch {position}: could not find {original_normalized[:60]!r}")

                if errors:
                    return {
                        "success": False,
                        "error": (
                            f"{len(errors)} of {len(patches)} patches did not match, so the file "
                            f"was left unchanged: {'; '.join(errors)}"
                        ),
                        "patches_applied": 0,
                        "patches_failed": len(errors),
                        "path": str(full_path),
                    }
            else:
                new_content = content
            
            full_path.write_text(new_content, encoding='utf-8')
            self._log_operation("update_file", {"path": path, "mode": mode})

            result = {"success": True, "path": str(full_path), "mode": mode}
            if mode == "patch":
                result["patches_applied"] = patches_applied
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def edit_file(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False
    ) -> Dict:
        """Replace an exact string in a file.

        Preferred over update_file for surgical edits: old_string is matched
        byte-for-byte (indentation and blank lines included) and the edit is
        REJECTED rather than guessed at when the match is missing or ambiguous.

        Args:
            path:        File to edit.
            old_string:  Exact text to find. Include enough surrounding lines
                         to make it unique within the file.
            new_string:  Replacement text. May be empty to delete old_string.
            replace_all: Replace every occurrence instead of requiring exactly one.

        Returns a unified diff of what changed.
        """
        try:
            full_path = self._resolve_path(path)

            if not full_path.exists():
                return {"success": False, "error": f"File not found: {path}"}
            if not full_path.is_file():
                return {"success": False, "error": f"Not a file: {path}"}
            if not old_string:
                return {"success": False, "error": "old_string must not be empty"}
            if old_string == new_string:
                return {"success": False, "error": "old_string and new_string are identical; nothing to do"}
            if full_path.stat().st_size > self.MAX_FILE_SIZE:
                return {"success": False, "error": f"File too large to edit: {path}"}

            original = full_path.read_text(encoding='utf-8')
            occurrences = original.count(old_string)

            if occurrences == 0:
                hint = ""
                # Distinguish "wrong indentation" from "genuinely absent": retry the
                # match ignoring per-line leading/trailing whitespace.
                loose = r'\s*\n\s*'.join(
                    re.escape(line.strip()) for line in old_string.strip().splitlines()
                )
                if loose and re.search(loose, original):
                    hint = (" A match exists ignoring whitespace - the indentation of "
                            "old_string does not match the file. Read the exact lines first.")
                return {
                    "success": False,
                    "error": f"old_string not found in {path}.{hint}",
                    "occurrences": 0
                }

            if occurrences > 1 and not replace_all:
                return {
                    "success": False,
                    "error": (
                        f"old_string is ambiguous: found {occurrences} occurrences in {path}. "
                        f"Add surrounding context to make it unique, or pass replace_all=True."
                    ),
                    "occurrences": occurrences
                }

            count = -1 if replace_all else 1
            updated = original.replace(old_string, new_string, count)
            replacements = occurrences if replace_all else 1

            full_path.write_text(updated, encoding='utf-8')

            diff_lines = list(difflib.unified_diff(
                original.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=f"{path} (before)",
                tofile=f"{path} (after)",
                n=3
            ))
            truncated = len(diff_lines) > self.MAX_DIFF_LINES
            if truncated:
                diff_lines = diff_lines[:self.MAX_DIFF_LINES]
                diff_lines.append(f"\n... [Diff truncated at {self.MAX_DIFF_LINES} lines] ...\n")

            self._log_operation("edit_file", {
                "path": path,
                "replacements": replacements,
                "replace_all": replace_all
            })

            return {
                "success": True,
                "path": str(full_path),
                "replacements": replacements,
                "occurrences": occurrences,
                "diff": "".join(diff_lines),
                "truncated": truncated
            }
        except UnicodeDecodeError:
            return {"success": False, "error": f"Cannot edit binary file: {path}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def delete_file(self, path: str) -> Dict:
        """Delete a file."""
        try:
            full_path = self._resolve_path(path)
            
            if not full_path.exists():
                return {"success": False, "error": f"File not found: {path}"}
            
            full_path.unlink()
            self._log_operation("delete_file", {"path": path})
            
            return {"success": True, "path": str(full_path)}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    # =========================================================================
    # Directory Operations
    # =========================================================================
    
    def move_file(self, source: str, destination: str, overwrite: bool = False) -> Dict:
        """Move or rename a file or directory within the workspace."""
        try:
            src = self._resolve_path(source)
            dst = self._resolve_path(destination)

            if not src.exists():
                return {"success": False, "error": f"Source not found: {source}"}
            if dst.exists():
                if not overwrite:
                    return {"success": False, "error": f"Destination exists: {destination}. Pass overwrite=True to replace."}
                if dst.is_dir():
                    shutil.rmtree(dst)
                else:
                    dst.unlink()
            if src == dst:
                return {"success": False, "error": "Source and destination are the same path"}

            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))

            self._log_operation("move_file", {"source": source, "destination": destination})

            return {
                "success": True,
                "source": str(src),
                "destination": str(dst),
                "type": "directory" if dst.is_dir() else "file"
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def copy_file(self, source: str, destination: str, overwrite: bool = False) -> Dict:
        """Copy a file or directory tree within the workspace."""
        try:
            src = self._resolve_path(source)
            dst = self._resolve_path(destination)

            if not src.exists():
                return {"success": False, "error": f"Source not found: {source}"}
            if src == dst:
                return {"success": False, "error": "Source and destination are the same path"}
            if dst.exists() and not overwrite:
                return {"success": False, "error": f"Destination exists: {destination}. Pass overwrite=True to replace."}

            dst.parent.mkdir(parents=True, exist_ok=True)

            if src.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(str(src), str(dst))
                kind = "directory"
            else:
                shutil.copy2(str(src), str(dst))
                kind = "file"

            self._log_operation("copy_file", {"source": source, "destination": destination})

            return {"success": True, "source": str(src), "destination": str(dst), "type": kind}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_directory(self, path: str, parents: bool = True) -> Dict:
        """Create a directory."""
        try:
            full_path = self._resolve_path(path)
            full_path.mkdir(parents=parents, exist_ok=True)
            self._log_operation("create_directory", {"path": path})
            return {"success": True, "path": str(full_path)}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def list_directory(
        self,
        path: str = ".",
        recursive: bool = False,
        include_hidden: bool = False
    ) -> Dict:
        """List directory contents with file limit protection."""
        try:
            full_path = self._resolve_path(path)
            
            if not full_path.exists():
                return {"success": False, "error": f"Directory not found: {path}"}
            
            items = []
            pattern = "**/*" if recursive else "*"
            file_count = 0
            
            for item in full_path.glob(pattern):
                if not include_hidden and item.name.startswith('.'):
                    continue
                
                file_count += 1
                if file_count > self.MAX_FILES_TO_SCAN:
                    return {
                        "success": False,
                        "error": f"Too many files ({file_count}+). Maximum: {self.MAX_FILES_TO_SCAN}. Use more specific path or pattern."
                    }
                
                rel_path = item.relative_to(self.workspace_root)
                items.append({
                    "path": str(rel_path),
                    "name": item.name,
                    "type": "directory" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else None
                })
            
            total_found = len(items)
            truncated = total_found > self.MAX_RESULTS_RETURNED
            if truncated:
                items = items[:self.MAX_RESULTS_RETURNED]
            
            return {
                "success": True,
                "path": str(full_path),
                "items": items,
                "count": len(items),
                "total_found": total_found,
                "truncated": truncated,
                "message": f"Showing {len(items)} of {total_found} items. Use more specific path to see all." if truncated else None
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_project_structure(self, project_name: str, structure: Dict) -> Dict:
        """Create a complete project structure from a specification."""
        try:
            created_items = []
            
            for dir_path in structure.get("directories", []):
                full_dir = f"{project_name}/{dir_path}"
                result = self.create_directory(full_dir)
                if result["success"]:
                    created_items.append({"type": "directory", "path": full_dir})
            
            for file_path, content in structure.get("files", {}).items():
                full_file = f"{project_name}/{file_path}"
                result = self.create_file(full_file, content)
                if result["success"]:
                    created_items.append({"type": "file", "path": full_file})
            
            self._log_operation("create_project_structure", {
                "project_name": project_name,
                "items_created": len(created_items)
            })
            
            return {
                "success": True,
                "project_name": project_name,
                "created_items": created_items
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get_project_tree(
        self,
        path: str = ".",
        max_depth: int = 5,
        exclude: Optional[List[str]] = None
    ) -> Dict:
        """Get a tree view of the project structure."""
        try:
            full_path = self._resolve_path(path)
            
            if exclude is None:
                exclude = ["node_modules", "__pycache__", ".git", ".next", ".venv", "venv", "dist", "build"]
            
            def build_tree(dir_path: Path, depth: int = 0) -> List[Dict]:
                if depth >= max_depth:
                    return []
                items = []
                try:
                    for item in sorted(dir_path.iterdir()):
                        if item.name in exclude or item.name.startswith('.'):
                            continue
                        rel_path = item.relative_to(self.workspace_root)
                        node = {
                            "name": item.name,
                            "path": str(rel_path),
                            "type": "directory" if item.is_dir() else "file"
                        }
                        if item.is_dir():
                            node["children"] = build_tree(item, depth + 1)
                        else:
                            node["size"] = item.stat().st_size
                        items.append(node)
                except PermissionError:
                    pass
                return items
            
            tree = build_tree(full_path)
            return {"success": True, "path": str(full_path), "tree": tree}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get_file_info(self, path: str) -> Dict:
        """Get detailed information about a file."""
        try:
            full_path = self._resolve_path(path)
            
            if not full_path.exists():
                return {"success": False, "error": f"File not found: {path}"}
            
            stat = full_path.stat()
            info = {
                "success": True,
                "path": str(full_path),
                "name": full_path.name,
                "size": stat.st_size,
                "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "is_file": full_path.is_file(),
                "is_directory": full_path.is_dir()
            }
            
            if full_path.is_file():
                content = full_path.read_text(encoding='utf-8')
                info["lines"] = len(content.splitlines())
                info["extension"] = full_path.suffix
            
            return info
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    # =========================================================================
    # Diff Operations
    # =========================================================================

    def diff_files(
        self,
        path: str,
        other: Optional[str] = None,
        content: Optional[str] = None,
        context_lines: int = 3
    ) -> Dict:
        """Show a unified diff.

        Two modes:
        - Compare two files:      diff_files(path="a.py", other="b.py")
        - Preview a pending edit: diff_files(path="a.py", content="<new full text>")

        Use the preview mode before update_file to confirm a rewrite only
        touches what you intend.
        """
        try:
            full_path = self._resolve_path(path)

            if not full_path.exists():
                return {"success": False, "error": f"File not found: {path}"}
            if not full_path.is_file():
                return {"success": False, "error": f"Not a file: {path}"}
            if full_path.stat().st_size > self.MAX_FILE_SIZE:
                return {
                    "success": False,
                    "error": f"File too large to diff: {path} "
                             f"({full_path.stat().st_size} bytes, max {self.MAX_FILE_SIZE})"
                }

            if (other is None) == (content is None):
                return {
                    "success": False,
                    "error": "Provide exactly one of 'other' (path to compare against) or 'content' (proposed new text)"
                }

            left = full_path.read_text(encoding='utf-8', errors='replace')
            left_label = path

            if other is not None:
                other_path = self._resolve_path(other)
                if not other_path.exists():
                    return {"success": False, "error": f"File not found: {other}"}
                if not other_path.is_file():
                    return {"success": False, "error": f"Not a file: {other}"}
                if other_path.stat().st_size > self.MAX_FILE_SIZE:
                    return {"success": False, "error": f"File too large to diff: {other}"}
                right = other_path.read_text(encoding='utf-8', errors='replace')
                right_label = other
            else:
                right = content
                right_label = f"{path} (proposed)"

            diff_lines = list(difflib.unified_diff(
                left.splitlines(keepends=True),
                right.splitlines(keepends=True),
                fromfile=left_label,
                tofile=right_label,
                n=max(0, context_lines)
            ))

            additions = sum(
                1 for line in diff_lines
                if line.startswith('+') and not line.startswith('+++')
            )
            deletions = sum(
                1 for line in diff_lines
                if line.startswith('-') and not line.startswith('---')
            )

            truncated = False
            if len(diff_lines) > self.MAX_DIFF_LINES:
                diff_lines = diff_lines[:self.MAX_DIFF_LINES]
                diff_lines.append(f"\n... [Diff truncated at {self.MAX_DIFF_LINES} lines] ...\n")
                truncated = True

            self._log_operation("diff_files", {
                "path": path,
                "other": other,
                "additions": additions,
                "deletions": deletions
            })

            return {
                "success": True,
                "identical": left == right,
                "from": left_label,
                "to": right_label,
                "diff": "".join(diff_lines),
                "additions": additions,
                "deletions": deletions,
                "truncated": truncated
            }
        except UnicodeDecodeError:
            return {"success": False, "error": f"Cannot diff binary file: {path}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # =========================================================================
    # Bash & Git
    # =========================================================================
    
    def bash(self, command: str, path: str = ".", timeout: int = 30) -> Dict:
        """Execute bash command. CI=true is set to skip interactive prompts."""
        try:
            full_path = self._resolve_path(path)
            env = os.environ.copy()
            env['CI'] = 'true'
            
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(full_path),
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout,
                env=env
            )
            
            self._log_operation("bash", {"command": command, "path": path})
            
            stdout = result.stdout
            stderr = result.stderr
            truncated = False
            message = None

            if len(stdout) > self.MAX_BASH_OUTPUT_CHARS:
                stdout = stdout[:self.MAX_BASH_OUTPUT_CHARS] + "\n... [Output truncated] ..."
                truncated = True
            if len(stderr) > self.MAX_BASH_OUTPUT_CHARS:
                stderr = stderr[:self.MAX_BASH_OUTPUT_CHARS] + "\n... [Error output truncated] ..."
                truncated = True
            if truncated:
                message = f"Output truncated to {self.MAX_BASH_OUTPUT_CHARS} characters."

            return {
                "success": result.returncode == 0,
                "stdout": stdout,
                "stderr": stderr,
                "returncode": result.returncode,
                "truncated": truncated,
                "message": message
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"Command timed out after {timeout}s"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _run(self, args: List[str], path: str = ".", timeout: int = 60) -> Dict:
        """Run a command from an argument list (no shell).

        Unlike bash(), arguments are passed to the process directly, so text
        containing quotes, $, backticks or newlines cannot be reinterpreted as
        shell syntax. Use this for any command built from model-supplied values.
        """
        try:
            full_path = self._resolve_path(path)
            env = os.environ.copy()
            env['CI'] = 'true'

            result = subprocess.run(
                args,
                cwd=str(full_path),
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout,
                env=env
            )

            stdout = result.stdout
            stderr = result.stderr
            truncated = False
            if len(stdout) > self.MAX_BASH_OUTPUT_CHARS:
                stdout = stdout[:self.MAX_BASH_OUTPUT_CHARS] + "\n... [Output truncated] ..."
                truncated = True
            if len(stderr) > self.MAX_BASH_OUTPUT_CHARS:
                stderr = stderr[:self.MAX_BASH_OUTPUT_CHARS] + "\n... [Error output truncated] ..."
                truncated = True

            return {
                "success": result.returncode == 0,
                "command": " ".join(args),
                "stdout": stdout,
                "stderr": stderr,
                "returncode": result.returncode,
                "truncated": truncated
            }
        except FileNotFoundError:
            return {"success": False, "error": f"Command not found: {args[0]}"}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"Command timed out after {timeout}s"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def _as_args(value: Union[str, List[str], None]) -> List[str]:
        """Normalize a str-or-list argument into a list of process arguments."""
        if value is None:
            return []
        if isinstance(value, str):
            return shlex.split(value) if value.strip() else []
        return [str(v) for v in value]

    def git_operations(self, operation: str, path: str = ".", **kwargs) -> Dict:
        """Perform git operations.

        Arguments are passed to git directly rather than through a shell, so a
        commit message containing quotes or $(...) is safe.
        """
        files = self._as_args(kwargs.get('files', '.')) or ['.']

        git_args = {
            "init": ["init"],
            "status": ["status"],
            "add": ["add", *files],
            "commit": ["commit", "-m", str(kwargs.get('message', 'Update'))],
            "branch": ["checkout", "-b", str(kwargs.get('name', 'feature'))],
            "checkout": ["checkout", str(kwargs.get('branch', 'main'))],
            "push": ["push"],
            "pull": ["pull"],
        }
        if operation not in git_args:
            return {
                "success": False,
                "error": (
                    f"Unknown git operation: {operation}. "
                    f"Available: {', '.join(git_args)}. "
                    f"For inspection use git_diff, git_log or git_show."
                )
            }

        self._log_operation("git_operations", {"operation": operation, "path": path})
        return self._run(["git", *git_args[operation]], path)

    def git_diff(
        self,
        path: str = ".",
        staged: bool = False,
        target: Optional[str] = None,
        files: Optional[Union[str, List[str]]] = None,
        stat_only: bool = False,
        context_lines: int = 3
    ) -> Dict:
        """Show uncommitted changes in a git repository.

        Args:
            path:          Repository path.
            staged:        Diff the index against HEAD instead of the working tree.
            target:        Diff against a commit/branch (e.g. "main", "HEAD~1").
            files:         Limit the diff to these paths.
            stat_only:     Return only the changed-file summary, not the full patch.
            context_lines: Lines of context around each hunk.
        """
        args = ["git", "diff"]
        if stat_only:
            # -U implies patch output and would defeat --stat, so it is omitted here.
            args.append("--stat")
        else:
            args.append(f"-U{max(0, context_lines)}")
        if staged:
            args.append("--staged")
        if target:
            args.append(str(target))
        path_args = self._as_args(files)
        if path_args:
            args.append("--")
            args.extend(path_args)

        result = self._run(args, path)
        if result.get("success"):
            diff_text = result.get("stdout", "")
            result["diff"] = diff_text
            result["has_changes"] = bool(diff_text.strip())
            result["additions"] = sum(
                1 for line in diff_text.splitlines()
                if line.startswith('+') and not line.startswith('+++')
            )
            result["deletions"] = sum(
                1 for line in diff_text.splitlines()
                if line.startswith('-') and not line.startswith('---')
            )
        self._log_operation("git_diff", {"path": path, "staged": staged, "target": target})
        return result

    def git_log(
        self,
        path: str = ".",
        count: int = 20,
        oneline: bool = True,
        files: Optional[Union[str, List[str]]] = None,
        author: Optional[str] = None
    ) -> Dict:
        """Show recent commit history.

        Args:
            path:    Repository path.
            count:   Number of commits to return.
            oneline: Compact one-line-per-commit format. False gives full messages.
            files:   Limit history to these paths.
            author:  Filter by author substring.
        """
        args = ["git", "log", f"-n{max(1, int(count))}"]
        if oneline:
            args.append("--pretty=format:%h %ad %an %s")
            args.append("--date=short")
        else:
            args.append("--stat")
        if author:
            args.append(f"--author={author}")
        path_args = self._as_args(files)
        if path_args:
            args.append("--")
            args.extend(path_args)

        result = self._run(args, path)
        if result.get("success"):
            result["commits"] = [l for l in result.get("stdout", "").splitlines() if l.strip()] if oneline else None
        self._log_operation("git_log", {"path": path, "count": count})
        return result

    def git_show(self, ref: str = "HEAD", path: str = ".", stat_only: bool = False) -> Dict:
        """Show a single commit: its message and the patch it introduced.

        Args:
            ref:       Commit-ish to show (e.g. "HEAD", "abc1234", "main~2").
            path:      Repository path.
            stat_only: Return only the changed-file summary, not the full patch.
        """
        args = ["git", "show"]
        if stat_only:
            args.append("--stat")
        args.append(str(ref))

        result = self._run(args, path)
        result["ref"] = ref
        self._log_operation("git_show", {"ref": ref, "path": path})
        return result

    # =========================================================================
    # Search Operations
    # =========================================================================
    
    def _compile_pattern(self, pattern: str, flags: int = 0):
        """Compile a caller-supplied regex, returning (regex, error_dict).

        Length is capped because a very long pattern is both a sign of a
        malformed call and a cheap way to build a pathological one.
        """
        if not pattern:
            return None, {"success": False, "error": "pattern must not be empty"}
        if len(pattern) > self.MAX_PATTERN_LENGTH:
            return None, {
                "success": False,
                "error": (
                    f"Pattern too long ({len(pattern)} chars, max "
                    f"{self.MAX_PATTERN_LENGTH}). Narrow the search instead."
                ),
            }
        try:
            return re.compile(pattern, flags), None
        except re.error as e:
            return None, {"success": False, "error": f"Invalid regex {pattern!r}: {e}"}

    def grep_files(
        self,
        pattern: str,
        path: str = ".",
        file_pattern: str = "*",
        case_sensitive: bool = False
    ) -> Dict:
        """Search for pattern in files with size and count limits."""
        try:
            full_path = self._resolve_path(path)
            matches = []
            flags = 0 if case_sensitive else re.IGNORECASE
            regex, pattern_error = self._compile_pattern(pattern, flags)
            if pattern_error:
                return pattern_error
            file_count = 0
            total_bytes_read = 0
            deadline = time.monotonic() + self.SCAN_TIME_BUDGET

            for file_path in full_path.rglob(file_pattern):
                if file_path.is_file():
                    if time.monotonic() > deadline:
                        return {
                            "success": False,
                            "error": (
                                f"Search exceeded {self.SCAN_TIME_BUDGET:.0f}s after scanning "
                                f"{file_count} files. Narrow the path, file_pattern or pattern."
                            ),
                            "files_scanned": file_count,
                        }
                    file_count += 1
                    if file_count > self.MAX_FILES_TO_SCAN:
                        return {"success": False, "error": f"Too many files to scan ({file_count}+). Maximum: {self.MAX_FILES_TO_SCAN}."}
                    try:
                        file_size = file_path.stat().st_size
                        if file_size > self.MAX_FILE_SIZE:
                            continue
                        total_bytes_read += file_size
                        if total_bytes_read > self.MAX_TOTAL_READ_SIZE:
                            return {"success": False, "error": "Total read size exceeded. Use more specific path."}
                        content = file_path.read_text(encoding='utf-8')
                        file_matches = [
                            {"line_number": i, "line": line.strip()}
                            for i, line in enumerate(content.split('\n'), 1)
                            if regex.search(line)
                        ]
                        if file_matches:
                            matches.append({"file": str(file_path.relative_to(self.workspace_root)), "matches": file_matches})
                    except:
                        continue
            
            total_matches = len(matches)
            truncated = total_matches > self.MAX_RESULTS_RETURNED
            if truncated:
                matches = matches[:self.MAX_RESULTS_RETURNED]
            
            return {
                "success": True,
                "pattern": pattern,
                "matches": matches,
                "total_files": len(matches),
                "total_matches_found": total_matches,
                "files_scanned": file_count,
                "bytes_read": total_bytes_read,
                "truncated": truncated,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def glob_search(self, pattern: str, path: str = ".") -> Dict:
        """Search for files matching glob pattern."""
        try:
            full_path = self._resolve_path(path)
            matches = [
                {
                    "path": str(file_path.relative_to(self.workspace_root)),
                    "name": file_path.name,
                    "type": "directory" if file_path.is_dir() else "file"
                }
                for file_path in full_path.glob(pattern)
            ]
            return {"success": True, "pattern": pattern, "matches": matches, "count": len(matches)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # Patterns that locate a symbol's *definition* across common languages.
    # Each entry is (kind, template) where {name} is the escaped symbol name.
    _SYMBOL_PATTERNS = [
        ("function",  r'^\s*(?:async\s+)?def\s+{name}\s*[\(:]'),
        ("class",     r'^\s*class\s+{name}\s*[\(:]'),
        ("function",  r'\bfunction\s+\*?\s*{name}\s*[\(<]'),
        ("class",     r'\bclass\s+{name}\s*[\{{<\s]'),
        ("variable",  r'\b(?:const|let|var)\s+{name}\s*[=:]'),
        ("function",  r'\b(?:const|let|var)\s+{name}\s*(?::[^=]+)?=\s*(?:async\s+)?(?:function\b|\(|<)'),
        ("function",  r'^\s*(?:export\s+)?(?:async\s+)?func\s+(?:\([^)]*\)\s*)?{name}\s*[\(<]'),
        ("type",      r'^\s*(?:export\s+)?type\s+{name}\b'),
        ("type",      r'^\s*(?:pub\s+)?(?:struct|enum|trait|interface|record)\s+{name}\b'),
        ("function",  r'^\s*(?:pub\s+)?(?:async\s+)?fn\s+{name}\s*[\(<]'),
    ]

    def find_symbol(
        self,
        name: str,
        path: str = ".",
        file_pattern: str = "*",
        include_references: bool = False
    ) -> Dict:
        """Find where a symbol is DEFINED (not merely mentioned).

        Cheaper and far less noisy than grep_files when the question is
        "where does X live?" — matches function/class/type/variable definitions
        across Python, JS/TS, Go, Rust, Java and C#.

        Args:
            name:               Exact symbol name (e.g. "FileSystemTools", "read_file").
            path:               Directory to search.
            file_pattern:       Glob filter, e.g. "*.py".
            include_references: Also count non-definition mentions per file.
        """
        try:
            if not name or not name.strip():
                return {"success": False, "error": "name must not be empty"}
            if len(name) > self.MAX_PATTERN_LENGTH:
                return {"success": False, "error": f"Symbol name too long ({len(name)} chars)"}

            full_path = self._resolve_path(path)
            escaped = re.escape(name.strip())

            compiled = []
            for kind, template in self._SYMBOL_PATTERNS:
                try:
                    compiled.append((kind, re.compile(template.format(name=escaped))))
                except re.error:
                    continue

            ref_regex = re.compile(r'\b' + escaped + r'\b')

            definitions = []
            references = []
            file_count = 0
            total_bytes_read = 0
            deadline = time.monotonic() + self.SCAN_TIME_BUDGET

            for file_path in full_path.rglob(file_pattern):
                if not file_path.is_file():
                    continue
                if time.monotonic() > deadline:
                    return {
                        "success": False,
                        "error": (
                            f"Search exceeded {self.SCAN_TIME_BUDGET:.0f}s after scanning "
                            f"{file_count} files. Narrow the path or file_pattern."
                        ),
                        "files_scanned": file_count,
                    }
                try:
                    rel_parts = file_path.relative_to(self.workspace_root).parts
                except ValueError:
                    continue
                # Only skip vendored dirs *inside* the workspace; the workspace's own
                # ancestors may legitimately be named 'build', 'target', etc.
                if any(part in self.SKIP_DIRS for part in rel_parts[:-1]):
                    continue

                file_count += 1
                if file_count > self.MAX_FILES_TO_SCAN:
                    return {
                        "success": False,
                        "error": f"Too many files to scan ({file_count}+). Maximum: {self.MAX_FILES_TO_SCAN}. Narrow path or file_pattern."
                    }
                try:
                    file_size = file_path.stat().st_size
                    if file_size > self.MAX_FILE_SIZE:
                        continue
                    total_bytes_read += file_size
                    if total_bytes_read > self.MAX_TOTAL_READ_SIZE:
                        return {"success": False, "error": "Total read size exceeded. Use a more specific path."}
                    content = file_path.read_text(encoding='utf-8')
                except Exception:
                    continue

                rel = str(file_path.relative_to(self.workspace_root))
                ref_count = 0

                for i, line in enumerate(content.split('\n'), 1):
                    matched_kind = None
                    for kind, regex in compiled:
                        if regex.search(line):
                            matched_kind = kind
                            break
                    if matched_kind:
                        definitions.append({
                            "file": rel,
                            "line_number": i,
                            "kind": matched_kind,
                            "line": line.strip()
                        })
                    elif include_references and ref_regex.search(line):
                        ref_count += 1

                if include_references and ref_count:
                    references.append({"file": rel, "reference_count": ref_count})

            truncated = len(definitions) > self.MAX_RESULTS_RETURNED
            if truncated:
                definitions = definitions[:self.MAX_RESULTS_RETURNED]

            result = {
                "success": True,
                "symbol": name,
                "definitions": definitions,
                "definition_count": len(definitions),
                "files_scanned": file_count,
                "truncated": truncated
            }
            if include_references:
                references.sort(key=lambda r: r["reference_count"], reverse=True)
                result["references"] = references[:self.MAX_RESULTS_RETURNED]
            if not definitions:
                result["message"] = (
                    f"No definition of '{name}' found. It may be imported from a dependency, "
                    f"defined dynamically, or in a language not covered - try grep_files."
                )
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    # Operations that WRITE, mapped to the argument(s) naming what they write.
    # Two tasks in the same wave that write the same path are serialized, so a
    # concurrent batch cannot silently lose one of the two updates.
    _WRITE_TARGET_ARGS = {
        "create_file": ("path",),
        "update_file": ("path",),
        "edit_file": ("path",),
        "delete_file": ("path",),
        "create_directory": ("path",),
        "move_file": ("source", "destination"),
        "copy_file": ("destination",),
        "create_project_structure": ("project_name",),
    }

    def _write_targets(self, operation: str, args: Dict) -> List[str]:
        """Resolved paths a batch task will write, for lock acquisition."""
        targets = []
        for arg_name in self._WRITE_TARGET_ARGS.get(operation, ()):
            value = args.get(arg_name)
            if not isinstance(value, str):
                continue
            try:
                targets.append(str(self._resolve_path(value)))
            except ValueError:
                # Escapes the workspace; the operation itself will refuse it.
                continue
        return sorted(set(targets))

    # Operations that take a directory and should inherit full_search_path
    # when a batch task does not name one explicitly.
    _PATH_DEFAULTED_OPS = frozenset({
        "bash", "git_operations", "glob_search", "git_diff", "git_log",
        "git_show", "find_symbol", "run_tests",
    })

    def _acquire_path_locks(self, targets: List[str]) -> List[threading.Lock]:
        """Fetch the locks for these paths, creating them on first use.

        Targets arrive pre-sorted so every caller takes locks in the same
        order, which is what keeps two multi-target tasks from deadlocking.
        """
        with self._path_locks_guard:
            return [self._path_locks.setdefault(t, threading.Lock()) for t in targets]

    def _batch_operation_map(self) -> Dict[str, Any]:
        """Operations callable from execute_batch."""
        return {
            "create_file": self.create_file,
            "read_file": self.read_file,
            "read_multiple_files": self.read_multiple_files,
            "update_file": self.update_file,
            "delete_file": self.delete_file,
            "create_directory": self.create_directory,
            "list_directory": self.list_directory,
            "bash": self.bash,
            "git_operations": self.git_operations,
            "grep_files": self.grep_files,
            "glob_search": self.glob_search,
            "get_file_info": self.get_file_info,
            "diff_files": self.diff_files,
            "edit_file": self.edit_file,
            "move_file": self.move_file,
            "copy_file": self.copy_file,
            "git_diff": self.git_diff,
            "git_log": self.git_log,
            "git_show": self.git_show,
            "find_symbol": self.find_symbol,
            "run_tests": self.run_tests,
            "web_fetch": self.web_fetch,
            "create_project_structure": self.create_project_structure,
            "get_project_tree": self.get_project_tree,
        }

    def execute_batch(
        self,
        tasks: str,
        allowed_tools: Optional[List[str]] = None,
        full_search_path: str = ".",
        max_workers: int = 4
    ) -> Dict:
        """Execute multiple tasks, in parallel where they are independent.

        tasks is a JSON string holding a list of task objects:

            {"operation": "read_file", "args": {"path": "a.py"}}

        Tasks run concurrently by default, so a task that consumes another
        task's output MUST declare the ordering with "id" and "depends_on":

            [{"id": "copy",  "operation": "copy_file",
              "args": {"source": "a.py", "destination": "b.py"}},
             {"id": "edit",  "operation": "edit_file", "depends_on": ["copy"],
              "args": {"path": "b.py", "old_string": "x", "new_string": "y"}}]

        Tasks are grouped into dependency waves: each wave runs in parallel and
        the next starts only when the previous has finished. A task whose
        dependency failed is not run at all and is reported as skipped, so a
        broken step does not cause a cascade of confusing downstream errors.

        Args:
            tasks:            JSON list of task objects.
            allowed_tools:    Restrict which operations the batch may call.
            full_search_path: Default path for directory-based operations.
            max_workers:      Maximum tasks running concurrently within a wave.
        """
        try:
            task_list = json.loads(tasks)
            if not isinstance(task_list, list):
                return {"success": False, "error": "Tasks must be a JSON list."}
            if not task_list:
                return {"success": True, "results": [], "total_tasks": 0,
                        "successes": 0, "failures": 0, "skipped": 0, "waves": 0}

            all_operations = self._batch_operation_map()
            operation_map = all_operations
            if allowed_tools:
                operation_map = {k: v for k, v in all_operations.items() if k in allowed_tools}

            # ---- Normalize tasks and their declared dependencies ----
            entries = []
            for index, task in enumerate(task_list):
                if not isinstance(task, dict):
                    return {"success": False, "error": f"Task at index {index} is not an object."}
                depends = task.get("depends_on") or []
                if isinstance(depends, str):
                    depends = [depends]
                if not isinstance(depends, list):
                    return {"success": False, "error": f"depends_on for task {index} must be a string or list."}
                entries.append({
                    "index": index,
                    "id": str(task.get("id", index)),
                    "task": task,
                    "depends_on": [str(d) for d in depends],
                })

            by_id = {}
            for entry in entries:
                if entry["id"] in by_id:
                    return {
                        "success": False,
                        "error": f"Duplicate task id '{entry['id']}'. Task ids must be unique."
                    }
                by_id[entry["id"]] = entry

            for entry in entries:
                unknown = [d for d in entry["depends_on"] if d not in by_id]
                if unknown:
                    return {
                        "success": False,
                        "error": f"Task '{entry['id']}' depends on unknown task id(s): {', '.join(unknown)}"
                    }
                if entry["id"] in entry["depends_on"]:
                    return {"success": False, "error": f"Task '{entry['id']}' depends on itself."}

            # ---- Group into dependency waves (Kahn layering) ----
            waves = []
            remaining = {e["id"]: set(e["depends_on"]) for e in entries}
            resolved = set()
            while remaining:
                ready = [tid for tid, deps in remaining.items() if not (deps - resolved)]
                if not ready:
                    return {
                        "success": False,
                        "error": (
                            "Circular dependency in batch tasks involving: "
                            f"{', '.join(sorted(remaining))}"
                        )
                    }
                ready.sort(key=lambda tid: by_id[tid]["index"])
                waves.append(ready)
                resolved.update(ready)
                for tid in ready:
                    remaining.pop(tid)

            # ---- Execute wave by wave ----
            def execute_task(entry) -> Dict:
                task_dict = entry["task"]
                operation = task_dict.get("operation")
                args = dict(task_dict.get("args") or {})
                if "path" not in args and operation in self._PATH_DEFAULTED_OPS:
                    args["path"] = full_search_path
                if operation not in operation_map:
                    if operation in all_operations:
                        return {
                            "success": False,
                            "error": f"Operation '{operation}' is not permitted by allowed_tools for this batch."
                        }
                    return {
                        "success": False,
                        "error": (
                            f"Unknown operation: {operation}. "
                            f"Available: {', '.join(sorted(operation_map))}"
                        )
                    }
                locks = self._acquire_path_locks(self._write_targets(operation, args))
                try:
                    with ExitStack() as stack:
                        for lock in locks:
                            stack.enter_context(lock)
                        return operation_map[operation](**args)
                except TypeError as e:
                    return {"success": False, "error": f"Bad arguments for {operation}: {e}"}
                except Exception as e:
                    return {"success": False, "error": str(e)}

            outcomes: Dict[str, Dict] = {}
            for wave in waves:
                runnable, skipped = [], []
                for tid in wave:
                    entry = by_id[tid]
                    blockers = [
                        d for d in entry["depends_on"]
                        if not outcomes.get(d, {}).get("success")
                    ]
                    if blockers:
                        skipped.append((tid, blockers))
                    else:
                        runnable.append(entry)

                for tid, blockers in skipped:
                    outcomes[tid] = {
                        "success": False,
                        "skipped": True,
                        "error": (
                            "Not run because its dependencies did not succeed: "
                            f"{', '.join(blockers)}"
                        )
                    }

                if not runnable:
                    continue
                if len(runnable) == 1:
                    entry = runnable[0]
                    outcomes[entry["id"]] = execute_task(entry)
                else:
                    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
                        futures = {executor.submit(execute_task, e): e["id"] for e in runnable}
                        for future in as_completed(futures):
                            outcomes[futures[future]] = future.result()

            # ---- Assemble results in submission order ----
            results = []
            for entry in entries:
                outcome = outcomes.get(entry["id"], {})
                if outcome.get("skipped"):
                    status = "skipped"
                elif outcome.get("success"):
                    status = "ok"
                else:
                    status = "failed"
                results.append({
                    "id": entry["id"],
                    "task": entry["task"],
                    "status": status,
                    **outcome
                })

            successes = sum(1 for r in results if r["status"] == "ok")
            skipped_count = sum(1 for r in results if r["status"] == "skipped")
            failures = len(results) - successes - skipped_count

            self._log_operation("execute_batch", {
                "total_tasks": len(entries),
                "waves": len(waves),
                "successes": successes,
                "failures": failures,
                "skipped": skipped_count,
            })

            return {
                "success": failures == 0 and skipped_count == 0,
                "results": results,
                "total_tasks": len(entries),
                "waves": len(waves),
                "successes": successes,
                "failures": failures,
                "skipped": skipped_count,
            }
        except json.JSONDecodeError as e:
            return {"success": False, "error": f"Invalid JSON in tasks parameter: {e}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # =========================================================================
    # Test Execution
    # =========================================================================

    def _detect_test_framework(self, target: Path) -> Optional[str]:
        """Guess the test framework for a directory."""
        root = target if target.is_dir() else target.parent
        for candidate in [root, *root.parents]:
            if not str(candidate).startswith(str(self.workspace_root)):
                break
            if (candidate / "pytest.ini").exists() or (candidate / "conftest.py").exists():
                return "pytest"
            if (candidate / "tox.ini").exists() or (candidate / "setup.cfg").exists():
                return "pytest"
            pyproject = candidate / "pyproject.toml"
            if pyproject.exists():
                try:
                    if "pytest" in pyproject.read_text(encoding='utf-8', errors='replace'):
                        return "pytest"
                except Exception:
                    pass
            if (candidate / "package.json").exists():
                try:
                    pkg = json.loads((candidate / "package.json").read_text(encoding='utf-8'))
                    if pkg.get("scripts", {}).get("test"):
                        return "npm"
                except Exception:
                    pass
            if candidate == self.workspace_root:
                break

        if target.is_dir() and any(target.rglob("test_*.py")):
            return "pytest"
        if target.is_file() and target.suffix == ".py":
            return "pytest"
        return None

    def run_tests(
        self,
        path: str = ".",
        framework: str = "auto",
        pattern: Optional[str] = None,
        timeout: int = 300,
        max_output_chars: int = 20_000
    ) -> Dict:
        """Run the project test suite and return a parsed summary.

        Returns pass/fail counts and the names of failing tests rather than the
        full run output, so a failing suite does not flood the context. Raw
        output is trimmed to the tail, where the failure detail lives.

        Args:
            path:             Test file or directory to run.
            framework:        "auto" (detect), "pytest", "npm", or "unittest".
            pattern:          Only run tests matching this name filter
                              (pytest -k / npm -t).
            timeout:          Seconds before the run is killed.
            max_output_chars: Cap on returned raw output.
        """
        try:
            full_path = self._resolve_path(path)
            if not full_path.exists():
                return {"success": False, "error": f"Path not found: {path}"}

            if framework == "auto":
                framework = self._detect_test_framework(full_path)
                if not framework:
                    return {
                        "success": False,
                        "error": (
                            "Could not detect a test framework. Pass framework='pytest', "
                            "'npm' or 'unittest' explicitly, or run the command via bash."
                        )
                    }

            rel_target = str(full_path.relative_to(self.workspace_root)) or "."

            if framework == "pytest":
                args = ["python", "-m", "pytest", rel_target, "-q", "--no-header", "-rf"]
                if pattern:
                    args.extend(["-k", pattern])
                cwd = "."
            elif framework == "unittest":
                args = ["python", "-m", "unittest", "discover", "-s", rel_target, "-v"]
                cwd = "."
            elif framework == "npm":
                args = ["npm", "test", "--silent"]
                if pattern:
                    args.extend(["--", "-t", pattern])
                cwd = rel_target if full_path.is_dir() else str(full_path.parent.relative_to(self.workspace_root))
            else:
                return {"success": False, "error": f"Unknown framework: {framework}. Use pytest, npm or unittest."}

            result = self._run(args, cwd or ".", timeout=timeout)

            if "error" in result and "stdout" not in result:
                result["framework"] = framework
                return result

            output = (result.get("stdout") or "") + "\n" + (result.get("stderr") or "")

            counts = {}
            for label in ("passed", "failed", "error", "errors", "skipped", "xfailed", "xpassed"):
                match = re.search(rf'(\d+)\s+{label}\b', output)
                if match:
                    key = "errors" if label in ("error", "errors") else label
                    counts[key] = int(match.group(1))

            failing = re.findall(r'^(?:FAILED|ERROR)\s+(\S+)', output, re.MULTILINE)

            trimmed = output.strip()
            output_truncated = len(trimmed) > max_output_chars
            if output_truncated:
                trimmed = "... [earlier output omitted] ...\n" + trimmed[-max_output_chars:]

            passed = result.get("returncode") == 0

            self._log_operation("run_tests", {
                "path": path, "framework": framework, "passed": passed, "counts": counts
            })

            return {
                "success": True,
                "passed": passed,
                "framework": framework,
                "command": result.get("command"),
                "returncode": result.get("returncode"),
                "counts": counts,
                "failing_tests": failing[:self.MAX_RESULTS_RETURNED],
                "failing_count": len(failing),
                "output": trimmed,
                "output_truncated": output_truncated
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # =========================================================================
    # Network
    # =========================================================================

    # Hostnames that must never be fetched regardless of what they resolve to.
    BLOCKED_HOSTS = frozenset({
        'localhost', 'localhost.localdomain', 'ip6-localhost', 'ip6-loopback',
        'metadata.google.internal', 'metadata', 'instance-data',
    })

    @classmethod
    def _check_public_url(cls, url: str) -> Optional[str]:
        """Return a refusal reason if url is not a safe public http(s) target.

        Resolves the host across BOTH address families with getaddrinfo, since
        gethostbyname is IPv4-only and would let http://[::1]/ through, and
        rejects the host if ANY resolved address is non-public.
        """
        try:
            parsed = urllib.parse.urlparse(url)
        except Exception as e:
            return f"Malformed URL {url!r}: {e}"

        if parsed.scheme not in ("http", "https"):
            return f"Only http and https URLs are supported (got '{parsed.scheme}')"

        host = (parsed.hostname or "").lower()
        if not host:
            return f"Malformed URL, no host: {url}"
        if host in cls.BLOCKED_HOSTS or host.endswith(".localhost") or host.endswith(".internal"):
            return f"Refusing to fetch local or internal address: {host}"

        # A bare IP literal is checked directly; a name is resolved first.
        addresses = []
        try:
            addresses.append(ipaddress.ip_address(host.strip('[]')))
        except ValueError:
            try:
                for family, _, _, _, sockaddr in socket.getaddrinfo(host, parsed.port or 80):
                    if family in (socket.AF_INET, socket.AF_INET6):
                        addresses.append(ipaddress.ip_address(sockaddr[0]))
            except socket.gaierror as e:
                return f"Could not resolve host {host}: {e}"

        if not addresses:
            return f"Could not resolve host {host} to any address"

        for ip in addresses:
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified
                    or (ip.version == 6 and (ip.ipv4_mapped or ip.is_site_local))):
                return f"Refusing to fetch non-public address: {host} -> {ip}"

        return None

    def web_fetch(
        self,
        url: str,
        max_chars: int = 50_000,
        timeout: int = 20,
        raw: bool = False
    ) -> Dict:
        """Fetch a URL and return its readable text content.

        For reading documentation, API references, changelogs or raw source
        files. HTML is reduced to text (scripts, styles and markup stripped);
        JSON and plain text are returned as-is.

        Requests to localhost and private network ranges are refused - this
        tool is for public documentation, not for probing internal services.

        Args:
            url:       http(s) URL to fetch.
            max_chars: Cap on returned text.
            timeout:   Seconds to wait for the response.
            raw:       Return the body without HTML-to-text conversion.
        """
        try:
            reason = self._check_public_url(url)
            if reason:
                return {"success": False, "error": reason}

            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "gemini-agent/1.0 (Python-urllib)",
                    "Accept": "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.8",
                }
            )

            opener = urllib.request.build_opener(_GuardedRedirectHandler(self._check_public_url))
            with opener.open(request, timeout=timeout) as response:
                content_type = (response.headers.get("Content-Type") or "").lower()
                charset = response.headers.get_content_charset() or "utf-8"
                body = response.read(max(max_chars, 1024) * 8)
                final_url = response.geturl()
                status = response.getcode()

            text = body.decode(charset, errors="replace")
            title = None
            looks_html = "html" in content_type or text.lstrip()[:100].lower().startswith(("<!doctype html", "<html"))

            if not raw and looks_html:
                title_match = re.search(r'<title[^>]*>(.*?)</title>', text, re.IGNORECASE | re.DOTALL)
                if title_match:
                    title = html.unescape(title_match.group(1)).strip()

                cleaned = re.sub(r'(?is)<(script|style|noscript|svg|head)\b.*?</\1>', ' ', text)
                cleaned = re.sub(r'(?is)<!--.*?-->', ' ', cleaned)
                cleaned = re.sub(r'(?i)<(br|/p|/div|/li|/h[1-6]|/tr)\s*/?>', '\n', cleaned)
                cleaned = re.sub(r'<[^>]+>', ' ', cleaned)
                cleaned = html.unescape(cleaned)
                cleaned = re.sub(r'[ \t\xa0]+', ' ', cleaned)
                cleaned = re.sub(r'\n\s*\n\s*\n+', '\n\n', cleaned)
                content = cleaned.strip()
                kind = "html"
            elif "json" in content_type and not raw:
                try:
                    content = json.dumps(json.loads(text), indent=2)
                except json.JSONDecodeError:
                    content = text
                kind = "json"
            else:
                content = text
                kind = "text"

            truncated = len(content) > max_chars
            if truncated:
                content = content[:max_chars] + "\n... [Content truncated] ..."

            self._log_operation("web_fetch", {"url": url, "status": status, "chars": len(content)})

            return {
                "success": True,
                "url": final_url,
                "status": status,
                "content_type": content_type,
                "kind": kind,
                "title": title,
                "content": content,
                "truncated": truncated
            }
        except urllib.error.HTTPError as e:
            return {"success": False, "error": f"HTTP {e.code} {e.reason} for {url}", "status": e.code}
        except urllib.error.URLError as e:
            return {"success": False, "error": f"Could not reach {url}: {e.reason}"}
        except socket.timeout:
            return {"success": False, "error": f"Request to {url} timed out after {timeout}s"}
        except Exception as e:
            return {"success": False, "error": str(e)}


class _GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Validates every redirect hop, not just the URL originally requested.

    urllib follows redirects transparently, so a permitted public host can
    otherwise 302 straight to the cloud metadata service or to loopback.
    """

    def __init__(self, validator):
        self._validator = validator

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        reason = self._validator(newurl)
        if reason:
            raise urllib.error.URLError(f"Refused redirect to {newurl}: {reason}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)
