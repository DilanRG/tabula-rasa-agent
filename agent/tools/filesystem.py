import os
import shutil
from agent.tools.base import Tool
from typing import Any, Dict

WORKSPACE = "/data/workspace"


class FilesystemTool(Tool):
    @property
    def name(self) -> str:
        return "filesystem"

    @property
    def description(self) -> str:
        return (
            "Read, write, list, and delete files in the workspace directory (/data/workspace/). "
            "This space persists across restarts. Use it to store data, notes, downloads, "
            "generated content, or anything else. "
            "Actions: list, read, write, append, delete, mkdir, tree."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "read", "write", "append", "delete", "mkdir", "tree"],
                    "description": "The operation to perform."
                },
                "path": {
                    "type": "string",
                    "description": "Relative path within workspace (e.g. 'notes/ideas.txt'). Defaults to root for list/tree."
                },
                "content": {
                    "type": "string",
                    "description": "File content (for write/append actions)."
                },
            },
            "required": ["action"]
        }

    def _resolve(self, path: str) -> str:
        """Resolve and validate a path is within the workspace."""
        if not path:
            return WORKSPACE
        resolved = os.path.normpath(os.path.join(WORKSPACE, path))
        if not (resolved + os.sep).startswith(WORKSPACE + os.sep) and resolved != WORKSPACE:
            raise ValueError(f"Path '{path}' is outside the workspace.")
        return resolved

    async def execute(self, action: str, path: str = "", content: str = "", **kwargs: Any) -> str:
        try:
            os.makedirs(WORKSPACE, exist_ok=True)

            if action == "list":
                target = self._resolve(path)
                if not os.path.isdir(target):
                    return f"Error: '{path or '/'}' is not a directory."
                entries = []
                for entry in sorted(os.listdir(target)):
                    full = os.path.join(target, entry)
                    if os.path.isdir(full):
                        entries.append(f"  [dir]  {entry}/")
                    else:
                        size = os.path.getsize(full)
                        entries.append(f"  [file] {entry}  ({size} bytes)")
                if not entries:
                    return f"Directory '{path or '/'}' is empty."
                return "\n".join(entries)

            elif action == "read":
                if not path:
                    return "Error: path is required for read."
                target = self._resolve(path)
                if not os.path.exists(target):
                    return f"Error: '{path}' does not exist."
                if os.path.isdir(target):
                    return f"Error: '{path}' is a directory. Use list instead."
                with open(target, "r", errors="replace") as f:
                    data = f.read(50000)  # Cap at 50KB
                return data

            elif action == "write":
                if not path:
                    return "Error: path is required for write."
                target = self._resolve(path)
                parent = os.path.dirname(target)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(target, "w") as f:
                    f.write(content)
                return f"Written {len(content)} bytes to '{path}'."

            elif action == "append":
                if not path:
                    return "Error: path is required for append."
                target = self._resolve(path)
                parent = os.path.dirname(target)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(target, "a") as f:
                    f.write(content)
                return f"Appended {len(content)} bytes to '{path}'."

            elif action == "delete":
                if not path:
                    return "Error: path is required for delete."
                target = self._resolve(path)
                if not os.path.exists(target):
                    return f"Error: '{path}' does not exist."
                if os.path.isdir(target):
                    shutil.rmtree(target)
                    return f"Deleted directory '{path}' and its contents."
                else:
                    os.remove(target)
                    return f"Deleted file '{path}'."

            elif action == "mkdir":
                if not path:
                    return "Error: path is required for mkdir."
                target = self._resolve(path)
                os.makedirs(target, exist_ok=True)
                return f"Directory '{path}' created."

            elif action == "tree":
                target = self._resolve(path)
                if not os.path.isdir(target):
                    return f"Error: '{path or '/'}' is not a directory."
                lines = []
                for root, dirs, files in os.walk(target):
                    level = root.replace(target, "").count(os.sep)
                    indent = "  " * level
                    dirname = os.path.basename(root) or "workspace/"
                    lines.append(f"{indent}{dirname}/")
                    for f in sorted(files):
                        lines.append(f"{indent}  {f}")
                    if len(lines) > 200:
                        lines.append("  ... (truncated)")
                        break
                return "\n".join(lines) if lines else "Workspace is empty."

            return f"Unknown action: {action}"
        except ValueError as e:
            return f"Security error: {e}"
        except Exception as e:
            return f"Filesystem error: {e}"
