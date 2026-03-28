"""
monitor.py — Real-time rich TUI monitoring dashboard for the Tabula Rasa agent.
Run this on the host machine while the agent container is running.

Controls:
  Page Up / Page Down  — Scroll the event log
  Home                 — Jump to oldest
  End / F              — Jump to newest (resume auto-tail)
  Q                    — Quit

Usage: python monitor.py
"""
import asyncio
import json
import sys
import msvcrt
from datetime import datetime
from collections import deque

import websockets
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

MONITOR_URI  = "ws://localhost:8766"
MAX_LOG_LINES = 2000   # Total history kept in memory
SCROLL_STEP   = 5      # Lines moved per Page Up/Down key

console = Console()

# ── Colour / icon palette ────────────────────────────────────────────────────
COLOURS = {
    "cycle_start":    "bold cyan",
    "cycle_end":      "bold green",
    "model_call":     "bold blue",
    "model_response": "blue",
    "tool_call":      "bold yellow",
    "tool_result":    "yellow",
    "journal_write":  "magenta",
    "chat_in":        "bold white",
    "chat_out":       "green",
    "error":          "bold red",
    "think":          "dim white",
    "status":         "dim cyan",
}
ICONS = {
    "cycle_start":    "⟳",
    "cycle_end":      "✓",
    "model_call":     "🧠",
    "model_response": "💬",
    "tool_call":      "🔧",
    "tool_result":    "📤",
    "journal_write":  "📓",
    "chat_in":        "👤",
    "chat_out":       "🤖",
    "error":          "❌",
    "think":          "💭",
    "status":         "ℹ ",
}


class MonitorState:
    def __init__(self):
        self.log: deque = deque(maxlen=MAX_LOG_LINES)
        self.scroll_offset: int = 0   # 0 = auto-tail; > 0 = lines from bottom
        self.auto_tail: bool = True
        self.connected: bool = False
        self.last_heartbeat: str = ""
        self.agent_uptime: str = "–"
        self.agent_idle: str = "–"
        self.agent_paused: bool = False
        self.stats = {
            "cycles":      0,
            "tool_calls":  0,
            "errors":      0,
            "last_model":  "–",
            "last_tool":   "–",
            "last_action": "–",
        }

    def scroll_up(self):
        self.auto_tail = False
        self.scroll_offset = min(self.scroll_offset + SCROLL_STEP, max(0, len(self.log) - 1))

    def scroll_down(self):
        self.scroll_offset = max(0, self.scroll_offset - SCROLL_STEP)
        if self.scroll_offset == 0:
            self.auto_tail = True

    def jump_top(self):
        self.auto_tail = False
        self.scroll_offset = max(0, len(self.log) - 1)

    def jump_bottom(self):
        self.scroll_offset = 0
        self.auto_tail = True

    def visible_lines(self, n: int):
        """Return the last n Rich Text lines, respecting scroll_offset."""
        log = list(self.log)
        total = len(log)
        if total == 0:
            return []
        if self.auto_tail:
            return log[-n:]
        # scroll_offset > 0: show a window ending (total - offset) lines from start
        end   = max(0, total - self.scroll_offset)
        start = max(0, end - n)
        return log[start:end]


state = MonitorState()


def format_event(evt: dict) -> Text:
    ts     = evt.get("ts", "??:??:??")
    etype  = evt.get("type", "status")
    colour = COLOURS.get(etype, "white")
    icon   = ICONS.get(etype, "•")

    line = Text()
    line.append(f"[{ts}] ", style="dim")
    line.append(f"{icon} ", style=colour)

    if etype == "cycle_start":
        line.append("── Autonomous cycle started ──", style=colour)
    elif etype == "cycle_end":
        line.append(f"── Cycle complete ({evt.get('duration','?')}s) ──", style=colour)
    elif etype == "model_call":
        model = evt.get("model", "?")
        ctx   = evt.get("ctx_tokens", "?")
        line.append("Calling ", style=colour)
        line.append(f"[{model}]", style="bold " + colour)
        line.append(f"   ctx={ctx} tok", style="dim")
    elif etype == "model_response":
        content   = str(evt.get("content", ""))
        has_tools = evt.get("has_tool_calls", False)
        if has_tools:
            line.append("Model chose to use tools", style=colour)
        else:
            line.append("Response: ", style=colour)
            line.append(content[:140] + ("…" if len(content) > 140 else ""), style="dim white")
    elif etype == "tool_call":
        tool = evt.get("tool", "?")
        args = json.dumps(evt.get("args", {}))
        if len(args) > 80:
            args = args[:80] + "…"
        line.append("Tool: ", style=colour)
        line.append(tool, style="bold " + colour)
        line.append(f"  {args}", style="dim")
    elif etype == "tool_result":
        tool   = evt.get("tool", "?")
        result = str(evt.get("result", ""))
        line.append(f"Result [{tool}]: ", style=colour)
        line.append(result[:140] + ("…" if len(result) > 140 else ""), style="dim white")
    elif etype == "journal_write":
        snippet = str(evt.get("snippet", ""))
        line.append("Journal ✍  ", style=colour)
        line.append(snippet[:120], style="dim white")
    elif etype == "chat_in":
        line.append("User: ", style=colour)
        line.append(str(evt.get("text", "")), style="bold white")
    elif etype == "chat_out":
        snippet = str(evt.get("snippet", ""))
        line.append("Agent: ", style=colour)
        line.append(snippet[:140], style="dim white")
    elif etype == "think":
        thought = str(evt.get("thought", ""))
        line.append(f"{thought[:200]}", style="dim italic")
    elif etype == "error":
        line.append(f"ERROR: {evt.get('message','')}", style=colour)
    else:
        line.append(str(evt)[:160], style="dim")

    return line


def build_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=3),
    )
    layout["body"].split_row(
        Layout(name="log", ratio=3),
        Layout(name="sidebar", ratio=1),
    )
    return layout


def render_header() -> Panel:
    conn   = "[bold green]● LIVE[/]" if state.connected else "[bold red]○ DISCONNECTED[/]"
    tail   = "[dim](auto-tail)[/]" if state.auto_tail else f"[dim](scroll -{state.scroll_offset})[/]"
    now    = datetime.now().strftime("%H:%M:%S")
    uptime = f"[dim]up {state.agent_uptime}[/dim]" if state.agent_uptime != "–" else ""
    paused = "  [bold yellow]⏸ CHAT[/]" if state.agent_paused else ""
    return Panel(
        f"[bold]Tabula Rasa — Agent Monitor[/bold]   {conn}{paused}  {tail}   {uptime}   [dim]{now}[/dim]",
        style="bold cyan",
        box=box.HORIZONTALS,
    )


def render_log(body_height: int) -> Panel:
    # Account for panel borders (2 lines) and padding
    usable = max(1, body_height - 2)
    lines  = state.visible_lines(usable)
    text   = Text()
    for line in lines:
        text.append_text(line)
        text.append("\n")
    scroll_hint = "" if state.auto_tail else f" ↑ {state.scroll_offset} lines from bottom"
    return Panel(
        text,
        title=f"[bold]Event Stream[/]{scroll_hint}",
        border_style="cyan",
        box=box.ROUNDED,
    )


def render_sidebar() -> Panel:
    grid = Table.grid(padding=(0, 1))
    grid.add_column(style="dim", no_wrap=True)
    grid.add_column(style="bold", no_wrap=True)
    rows = [
        ("Cycles",      str(state.stats["cycles"])),
        ("Tool calls",  str(state.stats["tool_calls"])),
        ("Errors",      str(state.stats["errors"])),
        ("Last model",  state.stats["last_model"][-20:]),
        ("Last tool",   state.stats["last_tool"]),
        ("Last action", state.stats["last_action"]),
        ("",            ""),
        ("Uptime",      state.agent_uptime),
        ("Idle (min)",  state.agent_idle),
        ("Heartbeat",   state.last_heartbeat or "–"),
        ("History",     str(len(state.log))),
    ]
    for label, value in rows:
        grid.add_row(label, value)
    return Panel(grid, title="[bold]Stats[/]", border_style="magenta", box=box.ROUNDED)


def render_footer() -> Panel:
    return Panel(
        "[dim]PgUp/PgDn[/dim] Scroll  │  [dim]Home[/dim] Oldest  │  [dim]End/F[/dim] Latest  │  [dim]Q[/dim] Quit",
        box=box.HORIZONTALS,
        style="dim",
    )


def update_stats(evt: dict):
    etype = evt.get("type")
    if etype == "heartbeat":
        state.last_heartbeat = datetime.now().strftime("%H:%M:%S")
        state.agent_uptime = evt.get("uptime", "–")
        state.agent_idle = str(evt.get("idle_min", "–"))
        state.agent_paused = evt.get("paused", False)
        return  # Don't log heartbeats to the event stream
    elif etype == "cycle_start":
        state.stats["cycles"] += 1
        state.stats["last_action"] = "Running cycle"
    elif etype == "tool_call":
        state.stats["tool_calls"] += 1
        state.stats["last_tool"]   = evt.get("tool", "?")
        state.stats["last_action"] = f"Tool: {evt.get('tool','?')}"
    elif etype == "error":
        state.stats["errors"] += 1
    elif etype == "model_call":
        state.stats["last_model"]  = evt.get("model", "?")
    elif etype == "chat_in":
        state.stats["last_action"] = "Chatting"


def refresh_all(layout: Layout, live: Live, body_height: int):
    layout["header"].update(render_header())
    layout["log"].update(render_log(body_height))
    layout["sidebar"].update(render_sidebar())
    live.refresh()


async def receive_events(layout: Layout, live: Live, body_height_ref: list):
    while True:
        try:
            state.connected = False
            async with websockets.connect(MONITOR_URI) as ws:
                state.connected = True
                state.log.append(Text("── Connected to agent monitor ──", style="bold green"))
                async for raw in ws:
                    evt = json.loads(raw)
                    update_stats(evt)
                    if evt.get("type") != "heartbeat":
                        state.log.append(format_event(evt))
                    refresh_all(layout, live, body_height_ref[0])
        except Exception as e:
            state.connected = False
            state.log.append(Text(f"── {type(e).__name__}: {e} — reconnecting in 1s ──", style="bold red"))
            refresh_all(layout, live, body_height_ref[0])
            await asyncio.sleep(1)


async def keyboard_listener(layout: Layout, live: Live, body_height_ref: list):
    """
    Non-blocking Windows key reader via msvcrt.
    Extended keys (arrows, PgUp, PgDn, Home, End) arrive as two bytes:
      b'\xe0' or b'\x00' followed by a scan code.
    """
    PGUP  = b'I'
    PGDN  = b'Q'
    HOME  = b'G'
    END   = b'O'
    loop  = asyncio.get_event_loop()

    def read_key():
        if msvcrt.kbhit():
            ch = msvcrt.getch()
            if ch in (b'\xe0', b'\x00'):          # extended key prefix
                ch2 = msvcrt.getch()
                return ("ext", ch2)
            return ("char", ch)
        return None

    while True:
        key = await loop.run_in_executor(None, read_key)
        if key is None:
            await asyncio.sleep(0.05)
            continue
        kind, val = key
        changed = False
        if kind == "char":
            if val.lower() in (b'q', b'\x1b'):    # Q or Escape
                raise KeyboardInterrupt
            elif val.lower() == b'f':              # F = jump to bottom
                state.jump_bottom(); changed = True
        elif kind == "ext":
            if val == PGUP:
                state.scroll_up();   changed = True
            elif val == PGDN:
                state.scroll_down(); changed = True
            elif val == HOME:
                state.jump_top();    changed = True
            elif val == END:
                state.jump_bottom(); changed = True
        if changed:
            refresh_all(layout, live, body_height_ref[0])


async def tick(layout: Layout, live: Live, body_height_ref: list):
    """Update the header clock every second (even when no events arrive)."""
    while True:
        await asyncio.sleep(1)
        layout["header"].update(render_header())
        live.refresh()


async def main():
    layout      = build_layout()
    # body_height is dynamic; we approximate 80% of console height
    body_height = [console.height - 6]

    layout["header"].update(render_header())
    layout["log"].update(render_log(body_height[0]))
    layout["sidebar"].update(render_sidebar())
    layout["footer"].update(render_footer())

    with Live(layout, screen=True, refresh_per_second=4) as live:
        # Recalculate body height each tick
        async def height_watcher():
            while True:
                body_height[0] = console.height - 6
                await asyncio.sleep(1)

        await asyncio.gather(
            receive_events(layout, live, body_height),
            keyboard_listener(layout, live, body_height),
            tick(layout, live, body_height),
            height_watcher(),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        console.print("\n[dim]Monitor closed.[/dim]")
