from duckduckgo_search import DDGS
from agent.tools.base import Tool
from typing import Any, Dict

class WebSearchTool(Tool):
    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Search the internet for information, news, or answers."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query."
                }
            },
            "required": ["query"]
        }

    async def execute(self, query: str) -> str:
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=5))
                return "\n\n".join([f"[{r['title']}]({r['href']})\n{r['body']}" for r in results])
        except Exception as e:
            return f"Search failed: {str(e)}"
