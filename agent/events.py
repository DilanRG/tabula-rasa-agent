"""
Event bus for the Tabula Rasa agent.
Emits structured events to connected monitor clients over a dedicated WebSocket.
"""
import asyncio
import json
from datetime import datetime
from typing import Any, Dict, List, Optional
import websockets

class EventBus:
    def __init__(self):
        self._subscribers: List[websockets.WebSocketServerProtocol] = []

    def subscribe(self, ws):
        self._subscribers.append(ws)

    def unsubscribe(self, ws):
        if ws in self._subscribers:
            self._subscribers.remove(ws)

    async def emit(self, event_type: str, data: Dict[str, Any]):
        """Emit an event to all connected monitor clients."""
        event = {
            "ts": datetime.now().strftime("%H:%M:%S"),
            "type": event_type,
            **data
        }
        dead = []
        for ws in self._subscribers:
            try:
                await ws.send(json.dumps(event))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.unsubscribe(ws)

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
