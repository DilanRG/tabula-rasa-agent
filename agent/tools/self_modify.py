import os
from agent.tools.base import Tool
from typing import Any, Dict

class SelfModifyTool(Tool):
    @property
    def name(self) -> str:
        return "self_modify"

    @property
    def description(self) -> str:
        return "Analyze, read, or modify your own source code to improve your capabilities."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "list", "write"],
                    "description": "The action to perform."
                },
                "filepath": {
                    "type": "string",
                    "description": "Relative path to the source file (e.g., 'agent/tools/new_tool.py')."
                },
                "content": {
                    "type": "string",
                    "description": "The new content for the file (for 'write' action)."
                }
            },
            "required": ["action"]
        }

    async def execute(self, action: str, filepath: str = "", content: str = "") -> str:
        allowed_dir = "/app/agent"

        if action == "list":
            files = []
            for root, dirs, filenames in os.walk(allowed_dir):
                for f in filenames:
                    files.append(os.path.relpath(os.path.join(root, f), "/app"))
            return "\n".join(files)

        # Security check for read/write — must be inside agent/ folder
        target_path = os.path.normpath(os.path.join("/app", filepath))
        if not (target_path + os.sep).startswith(allowed_dir + os.sep):
            return "Error: Security violation. You can only modify files inside the 'agent/' folder."

        if action == "read":
            if not os.path.exists(target_path):
                return "Error: File does not exist."
            with open(target_path, "r") as f:
                return f.read()

        if action == "write":
            if "config.yaml" in target_path or "Dockerfile" in target_path or ".env" in target_path:
                return "Error: Security violation. You cannot modify configuration or container system files."

            parent = os.path.dirname(target_path)
            if parent:                                   # only makedirs when there IS a parent dir
                os.makedirs(parent, exist_ok=True)
            with open(target_path, "w") as f:
                f.write(content)

            return f"Successfully wrote {filepath}. Call 'reboot' to apply changes."

        return "Invalid action."
