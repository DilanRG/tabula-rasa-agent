import trafilatura
from agent.tools.base import Tool
from typing import Any, Dict

class WebReadTool(Tool):
    @property
    def name(self) -> str:
        return "web_read"

    @property
    def description(self) -> str:
        return "Read the full text content of a URL."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch."
                }
            },
            "required": ["url"]
        }

    async def execute(self, url: str) -> str:
        try:
            downloaded = trafilatura.fetch_url(url)
            if not downloaded:
                return "Failed to fetch URL."
            text = trafilatura.extract(downloaded)
            return text[:8000] if text else "Could not extract text from page."
        except Exception as e:
            return f"Web read failed: {str(e)}"
