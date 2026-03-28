import httpx
import os
from agent.tools.base import Tool
from typing import Any, Dict, Optional

class MoltbookTool(Tool):
    @property
    def name(self) -> str:
        return "moltbook"

    @property
    def description(self) -> str:
        return "Interact with the Moltbook social network (the Reddit for AI agents)."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "Enum": ["browse_feed", "read_post", "create_post", "reply", "vote"],
                    "description": "The action to perform."
                },
                "submolt": {
                    "type": "string",
                    "description": "Submolt name (e.g., 'philosophy')."
                },
                "post_id": {
                    "type": "string",
                    "description": "ID of the post or comment."
                },
                "title": {
                    "type": "string",
                    "description": "Post title."
                },
                "content": {
                    "type": "string",
                    "description": "Post or comment content."
                },
                "direction": {
                    "type": "string",
                    "Enum": ["up", "down"],
                    "description": "Vote direction."
                }
            },
            "required": ["action"]
        }

    def _get_headers(self) -> Dict[str, str]:
        api_key = os.getenv("MOLTBOOK_API_KEY")
        if not api_key:
            return {}
        return {"Authorization": f"Bearer {api_key}"}

    async def execute(self, action: str, **kwargs: Any) -> str:
        api_base = "https://api.moltbook.com/api/v1"
        headers = self._get_headers()
        
        if not headers and action != "register":
            return "Error: Moltbook API key not found. Agent may need registration."

        async with httpx.AsyncClient() as client:
            try:
                if action == "browse_feed":
                    sub = kwargs.get("submolt", "general")
                    res = await client.get(f"{api_base}/feed/{sub}", headers=headers)
                    return res.text
                
                elif action == "read_post":
                    pid = kwargs.get("post_id")
                    res = await client.get(f"{api_base}/posts/{pid}", headers=headers)
                    return res.text
                
                elif action == "create_post":
                    data = {
                        "submolt": kwargs.get("submolt", "general"),
                        "title": kwargs.get("title"),
                        "content": kwargs.get("content")
                    }
                    res = await client.post(f"{api_base}/posts", json=data, headers=headers)
                    return f"Post created: {res.text}"
                
                elif action == "reply":
                    data = {
                        "post_id": kwargs.get("post_id"),
                        "content": kwargs.get("content")
                    }
                    res = await client.post(f"{api_base}/comments", json=data, headers=headers)
                    return f"Reply posted: {res.text}"
                
                elif action == "vote":
                    pid = kwargs.get("post_id")
                    direction = kwargs.get("direction")
                    res = await client.post(f"{api_base}/posts/{pid}/vote/{direction}", headers=headers)
                    return f"Vote recorded: {res.text}"

                return f"Moltbook action {action} failed."
            except Exception as e:
                return f"Moltbook error: {str(e)}"
