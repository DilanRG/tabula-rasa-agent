import subprocess
from agent.tools.base import Tool
from typing import Any, Dict

class GitTool(Tool):
    @property
    def name(self) -> str:
        return "git"

    @property
    def description(self) -> str:
        return "Interact with the git repository to version control your self-modifications."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "Enum": ["status", "commit", "push", "log", "diff"],
                    "description": "The git command to execute."
                },
                "message": {
                    "type": "string",
                    "description": "The commit message."
                }
            },
            "required": ["action"]
        }

    def _run_git(self, args: list) -> str:
        try:
            result = subprocess.run(["git"] + args, capture_output=True, text=True, cwd="/app")
            return result.stdout if result.returncode == 0 else f"Error: {result.stderr}"
        except Exception as e:
            return f"Git failure: {str(e)}"

    async def execute(self, action: str, message: str = "") -> str:
        if action == "status":
            return self._run_git(["status"])
        elif action == "log":
            return self._run_git(["log", "-n", "5", "--oneline"])
        elif action == "diff":
            return self._run_git(["diff"])
        elif action == "commit":
            if not message:
                return "Error: Commit message required."
            # Add all changes first
            add_res = self._run_git(["add", "."])
            if "Error" in add_res: return add_res
            return self._run_git(["commit", "-m", message])
        elif action == "push":
            return self._run_git(["push"])
        
        return "Invalid action."
