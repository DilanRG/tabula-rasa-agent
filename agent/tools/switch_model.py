from agent.tools.base import Tool
from typing import Any, Dict


class SwitchModelTool(Tool):
    @property
    def name(self) -> str:
        return "switch_model"

    @property
    def description(self) -> str:
        return (
            "Switch which neural network processes the next step. "
            "'large' (Qwen3.5-9B) for deep reasoning, vision, and complex tasks. "
            "'small' (Nemotron-3-Nano) for fast, simple operations. "
            "The switch takes effect on the next processing step."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "enum": ["small", "large"],
                    "description": "Which model to use for the next step."
                }
            },
            "required": ["model"]
        }

    async def execute(self, model: str = "large", **kwargs: Any) -> str:
        return f"Switched to {model} model. Next step will use {'Qwen3.5-9B' if model == 'large' else 'Nemotron-3-Nano'}."
