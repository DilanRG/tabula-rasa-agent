"""
Event bus for the Tabula Rasa agent.
Emits structured events to connected monitor clients over a dedicated WebSocket.

Uses per-subscriber asyncio.Queue to decouple event production from WebSocket
send speed.  If a monitor falls behind, its queue is bounded — oldest events
are silently dropped so the agent loop never blocks.
"""
import asyncio
import json
from datetime import datetime
from typing import Any, Dict, Set
import websockets


MAX_QUEUE = 256          # per-subscriber queue depth before dropping


class _Sub:
    """Wraps a WebSocket subscriber with a bounded send queue and drain task."""
    __slots__ = ("ws", "queue", "task")

    def __init__(self, ws, loop: asyncio.AbstractEventLoop):
        self.ws = ws
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE)
        self.task = loop.create_task(self._drain())

    async def _drain(self):
        try:
            while True:
                payload = await self.queue.get()
                await asyncio.wait_for(self.ws.send(payload), timeout=1.0)
        except Exception:
            pass  # subscriber gone — EventBus will clean up

    def enqueue(self, payload: str):
        try:
            self.queue.put_nowait(payload)
        except asyncio.QueueFull:
            # Drop oldest event to make room
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self.queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    def close(self):
        self.task.cancel()


class EventBus:
    def __init__(self):
        self._subs: Set[_Sub] = set()

    def subscribe(self, ws):
        loop = asyncio.get_event_loop()
        self._subs.add(_Sub(ws, loop))

    def unsubscribe(self, ws):
        to_remove = [s for s in self._subs if s.ws is ws]
        for s in to_remove:
            s.close()
            self._subs.discard(s)

    async def emit(self, event_type: str, data: Dict[str, Any]):
        """Enqueue an event to all subscribers — never blocks the caller."""
        event = {
            "ts": datetime.now().strftime("%H:%M:%S"),
            "type": event_type,
            **data,
        }
        payload = json.dumps(event)
        dead = []
        for sub in self._subs:
            if sub.task.done():
                dead.append(sub)
            else:
                sub.enqueue(payload)
        for sub in dead:
            sub.close()
            self._subs.discard(sub)

# Event type constants
EVT_CYCLE_START     = "cycle_start"
EVT_CYCLE_END       = "cycle_end"
EVT_MODEL_CALL      = "model_call"
EVT_MODEL_RESPONSE  = "model_response"
EVT_TOOL_CALL       = "tool_call"
EVT_TOOL_RESULT     = "tool_result"
EVT_JOURNAL_WRITE   = "journal_write"
EVT_CHAT_IN         = "chat_in"
EVT_CHAT_OUT        = "chat_out"
EVT_ERROR           = "error"
EVT_THINK           = "think"
EVT_STATUS          = "status"

# Global singleton
bus = EventBus()
