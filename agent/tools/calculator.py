"""Safe math expression evaluator for Moltbook verification challenges."""
import re
from agent.tools.base import Tool
from typing import Any, Dict


ALLOWED_CHARS = re.compile(r'^[\d\s\+\-\*\/\%\(\)\.\,]+$')


class CalculatorTool(Tool):
    @property
    def name(self) -> str:
        return "calculator"

    @property
    def description(self) -> str:
        return "Evaluate a math expression safely. Supports +, -, *, /, %, parentheses, and decimals."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Math expression to evaluate, e.g. '(12 + 8) * 3'.",
                },
            },
            "required": ["expression"],
        }

    async def execute(self, expression: str = "", **kwargs: Any) -> str:
        if not expression:
            return "Error: expression is required."
        expr = expression.replace(",", "")
        if not ALLOWED_CHARS.match(expr):
            return f"Error: expression contains disallowed characters. Only digits, operators (+−*/%), parentheses, and decimals are allowed."
        try:
            result = eval(expr, {"__builtins__": {}}, {})
            if isinstance(result, float) and result == int(result):
                result = int(result)
            return str(result)
        except ZeroDivisionError:
            return "Error: division by zero."
        except Exception as e:
            return f"Error evaluating expression: {e}"
