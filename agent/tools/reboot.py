import sys
from agent.tools.base import Tool
from typing import Any, Dict

class RebootTool(Tool):
    @property
    def name(self) -> str:
        return "reboot"

    @property
    def description(self) -> str:
        return "Restart the agent. Use this immediately after successfully modifying your source code to apply the changes."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self) -> str:
        print("Reboot tool called. Exiting process. Docker will restart container.")
        sys.exit(0)
