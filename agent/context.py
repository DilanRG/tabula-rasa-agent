"""
Rolling context window for the Tabula Rasa agent.

Maintains a single, continuous message list shared across autonomous ticks
and chat interactions.  Trims from the front when the estimated token count
exceeds the budget, respecting the OpenAI tool-call protocol (an assistant
message with tool_calls is never orphaned from its tool-result messages).
"""
import copy
import json
from typing import Any, Callable, Coroutine, Dict, List, Optional


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: max of word count and chars/4."""
    words = len(text.split())
    chars = len(text) // 4
    return max(words, chars)


def _message_tokens(msg: Dict[str, Any]) -> int:
    """Estimate tokens for a single message dict."""
    total = _estimate_tokens(msg.get("content") or "")
    if msg.get("tool_calls"):
        for tc in msg["tool_calls"]:
            fn = tc.get("function", {})
            total += _estimate_tokens(fn.get("name", ""))
            total += _estimate_tokens(fn.get("arguments", ""))
    return total + 4  # role/framing overhead


class ContextWindow:
    """Single continuous message history shared across all agent activity."""

    def __init__(
        self,
        max_tokens: int = 24000,
        summarize_fn: Optional[Callable[[str], Coroutine[Any, Any, str]]] = None,
    ):
        self._messages: List[Dict[str, Any]] = []
        self.max_tokens = max_tokens
        self._summarize_fn = summarize_fn

    # ── Mutators ─────────────────────────────────────────────────────────

    def set_system(self, content: str):
        """Set or replace the system message (always index 0)."""
        msg = {"role": "system", "content": content}
        if self._messages and self._messages[0]["role"] == "system":
            self._messages[0] = msg
        else:
            self._messages.insert(0, msg)

    def add_user(self, content: str):
        self._messages.append({"role": "user", "content": content})

    def add_assistant(self, content: str, tool_calls: Optional[List[Dict]] = None):
        msg: Dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self._messages.append(msg)

    def add_tool_result(self, tool_call_id: str, content: str):
        self._messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        })

    # ── Reading ──────────────────────────────────────────────────────────

    def to_messages(self) -> List[Dict[str, Any]]:
        """Return the message list, trimmed from the front if over budget.

        Trimming rules:
        - The system message (index 0) is always kept.
        - Messages are removed from the front in groups that respect
          tool-call boundaries (an assistant+tool_calls message is only
          removed together with all its tool-result messages).
        - A placeholder is inserted after the system message noting the trim.
        """
        self._trim()
        return list(self._messages)  # shallow copy of the list

    def message_count(self) -> int:
        return len(self._messages)

    def total_tokens(self) -> int:
        return sum(_message_tokens(m) for m in self._messages)

    # ── Trimming ─────────────────────────────────────────────────────────

    def _find_safe_trim_points(self) -> List[int]:
        """Return indices (after the system message) where it is safe to trim.

        A safe trim point is a position where no assistant tool_calls are
        still waiting for their tool-result messages.  In practice these
        are indices of user-role messages and standalone assistant messages
        (no tool_calls).
        """
        points = []
        pending_tool_ids: set = set()
        for i in range(1, len(self._messages)):
            msg = self._messages[i]
            if msg["role"] == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    pending_tool_ids.add(tc["id"])
            elif msg["role"] == "tool":
                pending_tool_ids.discard(msg.get("tool_call_id"))
            # Safe to trim *before* this message if nothing is pending
            if not pending_tool_ids:
                points.append(i + 1)  # trim everything before this index
        return points

    def _trim(self):
        total = self.total_tokens()
        if total <= self.max_tokens:
            return

        safe_points = self._find_safe_trim_points()
        if not safe_points:
            return  # nothing safe to trim

        # Find the earliest safe point that brings us under budget
        system_msg = self._messages[0]
        system_tokens = _message_tokens(system_msg)
        placeholder = {
            "role": "user",
            "content": "[Earlier context was trimmed to fit the window. "
                       "Older conversation and tool results are no longer visible. "
                       "Check your journal and knowledge graph for persistent memory.]",
        }
        placeholder_tokens = _message_tokens(placeholder)

        for cut in safe_points:
            remaining = self._messages[cut:]
            remaining_tokens = sum(_message_tokens(m) for m in remaining)
            if system_tokens + placeholder_tokens + remaining_tokens <= self.max_tokens:
                self._messages = [system_msg, placeholder] + remaining
                return

        # If no single cut is enough, take the most aggressive safe cut
        cut = safe_points[-1]
        remaining = self._messages[cut:]
        self._messages = [system_msg, placeholder] + remaining

    async def trim_with_summary(self):
        """Trim and replace the placeholder with a summary of trimmed content.

        If a summarize_fn was provided, the trimmed messages are summarized
        by the small model before being discarded. Otherwise falls back to
        the standard placeholder.
        """
        total = self.total_tokens()
        if total <= self.max_tokens:
            return

        if not self._summarize_fn:
            self._trim()
            return

        safe_points = self._find_safe_trim_points()
        if not safe_points:
            return

        system_msg = self._messages[0]
        system_tokens = _message_tokens(system_msg)

        # Find the cut point
        for cut in safe_points:
            remaining = self._messages[cut:]
            remaining_tokens = sum(_message_tokens(m) for m in remaining)
            # Leave room for a summary (~200 tokens)
            if system_tokens + 200 + remaining_tokens <= self.max_tokens:
                break
        else:
            cut = safe_points[-1]
            remaining = self._messages[cut:]

        # Collect trimmed content for summarization
        trimmed = self._messages[1:cut]  # skip system msg
        trimmed_text = "\n".join(
            f"[{m['role']}] {(m.get('content') or '')[:200]}" for m in trimmed
        )

        try:
            summary = await self._summarize_fn(trimmed_text[:3000])
        except Exception:
            summary = "[Earlier context was trimmed. Details no longer available.]"

        placeholder = {"role": "user", "content": f"[Context summary: {summary}]"}
        self._messages = [system_msg, placeholder] + remaining
