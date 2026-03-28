"""
Stimulus queue for the Tabula Rasa agent.

A stimulus is anything that should cause the agent to think: a timer tick,
a chat message arriving, a connection opening/closing.  The main loop blocks
on the queue; a timeout expiry is interpreted as "autonomous tick."
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Dict, Optional


class StimulusType(Enum):
    TICK = auto()           # Autonomous timer fired (no external event)
    CHAT_MESSAGE = auto()   # Human sent a message
    CHAT_CONNECT = auto()   # Chat WebSocket opened
    CHAT_DISCONNECT = auto()  # Chat WebSocket closed


@dataclass
class Stimulus:
    type: StimulusType
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)


class StimulusQueue:
    """Async queue that the main loop blocks on.

    `get(timeout)` returns the next stimulus, or None if the timeout expires
    (which the loop treats as an autonomous tick).
    """

    def __init__(self):
        self._queue: asyncio.Queue[Stimulus] = asyncio.Queue()

    def put(self, stimulus: Stimulus):
        self._queue.put_nowait(stimulus)

    async def get(self, timeout: float) -> Optional[Stimulus]:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def empty(self) -> bool:
        return self._queue.empty()
