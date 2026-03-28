from datetime import datetime
from agent.tools.base import Tool
from typing import Any, Dict

class ClockTool(Tool):
    @property
    def name(self) -> str:
        return "clock"

    @property
    def description(self) -> str:
        return "Get the current date, time, and timezone information."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self) -> str:
        now = datetime.now()
        return now.strftime("%Y-%m-%d %H:%M:%S")
