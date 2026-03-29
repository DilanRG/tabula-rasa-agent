"""
OODA cycle manager for Tabula Rasa Agent v3.

Structures each autonomous tick into Observe-Orient-Decide-Act-Evaluate phases.
Tracks goal history for loop detection and evaluation-driven tick adjustment.
"""
import os
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Deque, Dict, List, Optional


class CycleScore(float, Enum):
    PRODUCTIVE = 1.0
    NEUTRAL = 0.5
    STUCK = 0.0
    LOOP = -1.0


@dataclass
class CycleState:
    cycle_id: int = 0
    phase: str = "idle"
    goal: str = ""
    actions_taken: List[str] = field(default_factory=list)
    evaluation: str = ""
    score: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)


class CycleManager:
    """Manages OODA cycle phases and goal tracking."""

    def __init__(self, history_size: int = 10):
        self.goal_history: Deque[CycleState] = deque(maxlen=history_size)
        self.current: Optional[CycleState] = None

    def start_cycle(self, cycle_id: int) -> CycleState:
        self.current = CycleState(cycle_id=cycle_id, phase="observe")
        return self.current

    def set_goal(self, goal: str):
        if self.current:
            self.current.goal = goal
            self.current.phase = "act"

    def record_action(self, tool_name: str):
        if self.current:
            self.current.actions_taken.append(tool_name)

    def complete_cycle(self, evaluation: str, score: float):
        if self.current:
            self.current.evaluation = evaluation
            self.current.score = score
            self.current.phase = "done"
            self.goal_history.append(self.current)
            self.current = None

    # ── Session state for observe phase ─────────────────────────────────

    async def build_session_state(
        self,
        tools: Dict[str, Any],
        include_moltbook: bool = True,
    ) -> str:
        """Build compact session state (~300 tokens) for the observe phase."""
        parts = []

        # Last 3 journal entries (compact)
        if "journal" in tools:
            journal = await tools["journal"].execute(action="read_today")
            if journal and journal != "No entries for today yet.":
                lines = journal.strip().split("\n")
                # Get last ~15 lines (roughly 3 entries)
                tail = lines[-15:] if len(lines) > 15 else lines
                parts.append("Recent journal:\n" + "\n".join(tail))

        # KG stats (one line)
        if "knowledge_graph" in tools:
            try:
                stats = await tools["knowledge_graph"].execute(action="stats")
                parts.append(f"KG: {stats}")
            except Exception:
                pass

        # Moltbook notifications (throttled externally)
        if include_moltbook and "moltbook" in tools and os.environ.get("MOLTBOOK_API_KEY"):
            try:
                home = await tools["moltbook"].execute(action="home")
                parts.append(f"Moltbook:\n{home}")
            except Exception:
                pass

        # Goal history (last 3)
        recent_goals = list(self.goal_history)[-3:]
        if recent_goals:
            goal_lines = []
            for g in recent_goals:
                label = "PRODUCTIVE" if g.score >= 0.8 else "NEUTRAL" if g.score >= 0.3 else "STUCK" if g.score >= 0 else "LOOP"
                goal_lines.append(f"  [{label}] {g.goal[:80]}")
            parts.append("Recent goals:\n" + "\n".join(goal_lines))

        return "\n\n".join(parts) if parts else "[No session state available]"

    # ── Observe prompt ──────────────────────────────────────────────────

    def build_observe_prompt(self, session_state: str, tick_time: str) -> str:
        return (
            f"[Tick at {tick_time}]\n\n"
            f"{session_state}"
        )

    # ── Evaluate prompt ─────────────────────────────────────────────────

    def build_evaluate_prompt(self, goal: str, content: str, actions: List[str]) -> str:
        action_str = ", ".join(actions[:10]) if actions else "none"
        return (
            f"Evaluate this cycle.\n"
            f"Goal: {goal}\n"
            f"Actions: {action_str}\n"
            f"Result: {content[:300]}\n\n"
            f"Rate as one of: PRODUCTIVE, NEUTRAL, STUCK, LOOP\n"
            f"Reply with ONLY the rating word."
        )

    # ── Score parsing ───────────────────────────────────────────────────

    @staticmethod
    def parse_score(evaluation_text: str) -> float:
        text = evaluation_text.strip().upper()
        if "PRODUCTIVE" in text:
            return CycleScore.PRODUCTIVE
        elif "NEUTRAL" in text:
            return CycleScore.NEUTRAL
        elif "LOOP" in text:
            return CycleScore.LOOP
        elif "STUCK" in text:
            return CycleScore.STUCK
        return CycleScore.NEUTRAL  # default

    # ── Loop detection ──────────────────────────────────────────────────

    def detect_loop(self) -> bool:
        """Check if the last 3 goals are suspiciously similar."""
        recent = list(self.goal_history)[-3:]
        if len(recent) < 3:
            return False
        goals = [g.goal.lower().strip() for g in recent]
        # Check if all 3 goals share >60% words
        word_sets = [set(g.split()) for g in goals]
        if not all(word_sets):
            return False
        common = word_sets[0]
        for ws in word_sets[1:]:
            common = common & ws
        avg_size = sum(len(ws) for ws in word_sets) / len(word_sets)
        if avg_size == 0:
            return False
        return len(common) / avg_size > 0.6

    # ── Tick interval from score ────────────────────────────────────────

    def compute_tick_interval(self, base: float, score: float) -> float:
        if score >= 0.8:
            return base
        elif score >= 0.3:
            return base * 1.5
        elif score >= 0.0:
            return base * 2.0
        else:  # LOOP
            return base * 3.0

    # ── Journal formatting ──────────────────────────────────────────────

    def format_journal_entry(self, goal: str, content: str, score: float, tools_called: int = 0) -> Optional[str]:
        """Format a journal entry — returns None if cycle was unproductive."""
        if score < 0.5 and tools_called == 0:
            return None
        return f"[GOAL] {goal}\n[RESULT] {content[:200]}"
