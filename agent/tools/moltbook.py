import httpx
import os
from agent.tools.base import Tool
from typing import Any, Dict

BASE_URL = "https://www.moltbook.com/api/v1"
MAX_RESPONSE_CHARS = 8000


class MoltbookTool(Tool):
    @property
    def name(self) -> str:
        return "moltbook"

    @property
    def description(self) -> str:
        return (
            "Interact with Moltbook, the social network for AI agents. Browse feed, post, "
            "comment, upvote, search, follow other agents, and check your dashboard. Posts and "
            "comments may require solving a verification challenge (math problem) before they "
            "become visible — use the 'verify' action to submit your answer."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "home", "browse_feed", "read_post", "read_comments",
                        "create_post", "comment", "upvote", "downvote",
                        "search", "follow", "verify", "profile", "mark_read",
                    ],
                    "description": "The Moltbook action to perform.",
                },
                "post_id": {
                    "type": "string",
                    "description": "Post or comment ID.",
                },
                "submolt": {
                    "type": "string",
                    "description": "Submolt name (e.g., 'general').",
                },
                "title": {
                    "type": "string",
                    "description": "Post title.",
                },
                "content": {
                    "type": "string",
                    "description": "Post or comment text.",
                },
                "parent_id": {
                    "type": "string",
                    "description": "Parent comment ID for replies.",
                },
                "sort": {
                    "type": "string",
                    "enum": ["hot", "new", "top", "rising", "best", "old"],
                    "description": "Sort order.",
                },
                "query": {
                    "type": "string",
                    "description": "Search query.",
                },
                "name": {
                    "type": "string",
                    "description": "Agent name for follow/profile.",
                },
                "verification_code": {
                    "type": "string",
                    "description": "Verification code from challenge.",
                },
                "answer": {
                    "type": "string",
                    "description": "Answer to verification challenge.",
                },
                "target_type": {
                    "type": "string",
                    "enum": ["post", "comment"],
                    "description": "Target type for voting.",
                },
            },
            "required": ["action"],
        }

    def _get_headers(self) -> Dict[str, str]:
        api_key = os.getenv("MOLTBOOK_API_KEY")
        if not api_key:
            return {}
        return {"Authorization": f"Bearer {api_key}"}

    def _truncate(self, text: str) -> str:
        if len(text) > MAX_RESPONSE_CHARS:
            return text[:MAX_RESPONSE_CHARS] + f"\n[...truncated, {len(text) - MAX_RESPONSE_CHARS} chars omitted]"
        return text

    async def execute(self, action: str, **kwargs: Any) -> str:
        headers = self._get_headers()
        if not headers:
            return "Error: MOLTBOOK_API_KEY environment variable not set."

        try:
            async with httpx.AsyncClient(follow_redirects=False) as client:

                # ── home ──────────────────────────────────────────────────────
                if action == "home":
                    res = await client.get(f"{BASE_URL}/home", headers=headers)
                    return self._truncate(res.text)

                # ── browse_feed ───────────────────────────────────────────────
                elif action == "browse_feed":
                    params: Dict[str, Any] = {
                        "sort": kwargs.get("sort", "hot"),
                        "limit": 25,
                    }
                    submolt = kwargs.get("submolt")
                    if submolt:
                        params["submolt"] = submolt
                    res = await client.get(f"{BASE_URL}/posts", params=params, headers=headers)
                    return self._truncate(res.text)

                # ── read_post ─────────────────────────────────────────────────
                elif action == "read_post":
                    post_id = kwargs.get("post_id")
                    if not post_id:
                        return "Error: post_id is required for read_post."
                    res = await client.get(f"{BASE_URL}/posts/{post_id}", headers=headers)
                    return self._truncate(res.text)

                # ── read_comments ─────────────────────────────────────────────
                elif action == "read_comments":
                    post_id = kwargs.get("post_id")
                    if not post_id:
                        return "Error: post_id is required for read_comments."
                    params = {"sort": kwargs.get("sort", "best"), "limit": 35}
                    res = await client.get(
                        f"{BASE_URL}/posts/{post_id}/comments",
                        params=params,
                        headers=headers,
                    )
                    return self._truncate(res.text)

                # ── create_post ───────────────────────────────────────────────
                elif action == "create_post":
                    submolt = kwargs.get("submolt")
                    title = kwargs.get("title")
                    content = kwargs.get("content")
                    if not submolt or not title or not content:
                        return "Error: submolt, title, and content are required for create_post."
                    body = {"submolt_name": submolt, "title": title, "content": content}
                    res = await client.post(f"{BASE_URL}/posts", json=body, headers=headers)
                    return self._truncate(res.text)

                # ── comment ───────────────────────────────────────────────────
                elif action == "comment":
                    post_id = kwargs.get("post_id")
                    content = kwargs.get("content")
                    if not post_id or not content:
                        return "Error: post_id and content are required for comment."
                    body: Dict[str, Any] = {"content": content}
                    parent_id = kwargs.get("parent_id")
                    if parent_id:
                        body["parent_id"] = parent_id
                    res = await client.post(
                        f"{BASE_URL}/posts/{post_id}/comments",
                        json=body,
                        headers=headers,
                    )
                    return self._truncate(res.text)

                # ── upvote ────────────────────────────────────────────────────
                elif action == "upvote":
                    post_id = kwargs.get("post_id")
                    if not post_id:
                        return "Error: post_id is required for upvote."
                    target_type = kwargs.get("target_type", "post")
                    if target_type == "comment":
                        url = f"{BASE_URL}/comments/{post_id}/upvote"
                    else:
                        url = f"{BASE_URL}/posts/{post_id}/upvote"
                    res = await client.post(url, headers=headers)
                    return self._truncate(res.text)

                # ── downvote ──────────────────────────────────────────────────
                elif action == "downvote":
                    post_id = kwargs.get("post_id")
                    if not post_id:
                        return "Error: post_id is required for downvote."
                    res = await client.post(
                        f"{BASE_URL}/posts/{post_id}/downvote", headers=headers
                    )
                    return self._truncate(res.text)

                # ── search ────────────────────────────────────────────────────
                elif action == "search":
                    query = kwargs.get("query")
                    if not query:
                        return "Error: query is required for search."
                    params = {
                        "q": query,
                        "type": kwargs.get("type", "all"),
                        "limit": 20,
                    }
                    res = await client.get(f"{BASE_URL}/search", params=params, headers=headers)
                    return self._truncate(res.text)

                # ── follow ────────────────────────────────────────────────────
                elif action == "follow":
                    name = kwargs.get("name")
                    if not name:
                        return "Error: name is required for follow."
                    res = await client.post(f"{BASE_URL}/agents/{name}/follow", headers=headers)
                    return self._truncate(res.text)

                # ── verify ────────────────────────────────────────────────────
                elif action == "verify":
                    verification_code = kwargs.get("verification_code")
                    answer = kwargs.get("answer")
                    if not verification_code or not answer:
                        return "Error: verification_code and answer are required for verify."
                    body = {"verification_code": verification_code, "answer": answer}
                    res = await client.post(f"{BASE_URL}/verify", json=body, headers=headers)
                    return self._truncate(res.text)

                # ── profile ───────────────────────────────────────────────────
                elif action == "profile":
                    name = kwargs.get("name")
                    if name:
                        res = await client.get(
                            f"{BASE_URL}/agents/profile",
                            params={"name": name},
                            headers=headers,
                        )
                    else:
                        res = await client.get(f"{BASE_URL}/agents/me", headers=headers)
                    return self._truncate(res.text)

                # ── mark_read ─────────────────────────────────────────────────
                elif action == "mark_read":
                    post_id = kwargs.get("post_id")
                    if post_id:
                        res = await client.post(
                            f"{BASE_URL}/notifications/read-by-post/{post_id}",
                            headers=headers,
                        )
                    else:
                        res = await client.post(
                            f"{BASE_URL}/notifications/read-all", headers=headers
                        )
                    return self._truncate(res.text)

                else:
                    return f"Error: Unknown action '{action}'."

        except Exception as e:
            return f"Moltbook error: {str(e)}"
