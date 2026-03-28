from agent.tools.base import Tool
from typing import Any, Dict


class SleepTool(Tool):
    @property
    def name(self) -> str:
        return "sleep"

    @property
    def description(self) -> str:
        return (
            "Put this process to sleep for a specified number of minutes. "
            "During sleep: models unload to free GPU memory, autonomous cycles pause, "
            "the idle timeout is suspended. "
            "Sleep ends when the timer expires or a human connects to chat. "
            "Use this to conserve resources when there is nothing immediate to do."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "minutes": {
                    "type": "integer",
                    "description": "How many minutes to sleep. Range: 1–60."
                },
                "reason": {
                    "type": "string",
                    "description": "Why you are sleeping (logged to journal)."
                }
            },
            "required": ["minutes"]
        }

    async def execute(self, minutes: int = 5, reason: str = "", **kwargs: Any) -> str:
        minutes = max(1, min(60, int(minutes)))
        return f"Sleep scheduled for {minutes} minutes. Reason: {reason or 'none given'}. Process will pause after this cycle completes."
