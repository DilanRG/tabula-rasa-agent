"""
monitor.py — Real-time rich TUI monitoring dashboard for the Tabula Rasa agent.
Run this on the host machine while the agent container is running.

Usage: python monitor.py
"""
import asyncio
import json
import sys
from datetime import datetime
from collections import deque

import websockets
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.style import Style
from rich import box

MONITOR_URI = "ws://localhost:8766"
MAX_LOG_LINES = 60

console = Console()

# ── Colour palette ──────────────────────────────────────────────────────────
COLOURS = {
    "cycle_start":      "bold cyan",
    "cycle_end":        "bold green",
    "model_call":       "bold blue",
    "model_response":   "blue",
    "tool_call":        "bold yellow",
    "tool_result":      "yellow",
    "journal_write":    "magenta",
    "kg_update":        "bright_magenta",
    "chat_in":          "bold white",
    "chat_out":         "green",
    "error":            "bold red",
    "think":            "dim white",
    "status":           "dim cyan",
}

ICONS = {
    "cycle_start":      "⟳",
    "cycle_end":        "✓",
    "model_call":       "🧠",
    "model_response":   "💬",
    "tool_call":        "🔧",
    "tool_result":      "📤",
    "journal_write":    "📓",
    "kg_update":        "🕸 ",
    "chat_in":          "👤",
    "chat_out":         "🤖",
    "error":            "❌",
    "think":            "💭",
    "status":           "ℹ ",
}

class MonitorState:
    def __init__(self):
        self.log: deque = deque(maxlen=MAX_LOG_LINES)
        self.stats = {
            "uptime": "–",
            "cycles": 0,
            "tool_calls": 0,
            "errors": 0,
            "last_model": "–",
            "last_tool": "–",
            "last_action": "–",
        }
        self.connected = False
        self.current_think = ""

state = MonitorState()

def format_event(evt: dict) -> Text:
    """Format a single event as a coloured Rich Text line."""
    ts = evt.get("ts", "??:??:??")
    etype = evt.get("type", "status")
    colour = COLOURS.get(etype, "white")
    icon = ICONS.get(etype, "•")

    line = Text()
    line.append(f"[{ts}] ", style="dim")
    line.append(f"{icon} ", style=colour)

    if etype == "cycle_start":
        line.append("── Autonomous cycle started ──", style=colour)

    elif etype == "cycle_end":
        line.append(f"── Cycle complete ({evt.get('duration','?')}s) ──", style=colour)

    elif etype == "model_call":
        model = evt.get("model", "?")
        ctx_tokens = evt.get("ctx_tokens", "?")
        line.append(f"Calling ", style=colour)
        line.append(f"[{model}]", style="bold " + colour)
        line.append(f"   ctx={ctx_tokens} tokens", style="dim")

    elif etype == "model_response":
        content = evt.get("content", "")[:120]
        has_tools = evt.get("has_tool_calls", False)
        if has_tools:
            line.append("Model chose to use tools", style=colour)
        else:
            line.append(f"Response: ", style=colour)
            line.append(content + ("…" if len(evt.get("content","")) > 120 else ""), style="dim white")

    elif etype == "tool_call":
        tool = evt.get("tool", "?")
        args = json.dumps(evt.get("args", {}))
        if len(args) > 80:
            args = args[:80] + "…"
        line.append(f"Tool: ", style=colour)
        line.append(tool, style="bold " + colour)
        line.append(f"  args={args}", style="dim")

    elif etype == "tool_result":
        tool = evt.get("tool", "?")
        result = str(evt.get("result", ""))[:120]
        line.append(f"Result [{tool}]: ", style=colour)
        line.append(result + ("…" if len(str(evt.get("result",""))) > 120 else ""), style="dim white")

    elif etype == "journal_write":
        snippet = evt.get("snippet", "")[:100]
        line.append("Journal: ", style=colour)
        line.append(snippet, style="dim white")

    elif etype == "kg_update":
        line.append(f"KG: {evt.get('subject')} → {evt.get('predicate')} → {evt.get('object')}", style=colour)

    elif etype == "chat_in":
        line.append("User: ", style=colour)
        line.append(evt.get("text", ""), style="bold white")

    elif etype == "chat_out":
        snippet = evt.get("snippet", "")[:120]
        line.append("Agent replies: ", style=colour)
        line.append(snippet, style="dim white")

    elif etype == "think":
        # Chain-of-thought reasoning (stripped <think> blocks)
        thought = evt.get("thought", "")[:200]
        line.append(f"💭 {thought}", style="dim italic")

    elif etype == "error":
        line.append(f"ERROR: {evt.get('message','')}", style=colour)

    else:
        line.append(str(evt), style="dim")

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
    status = "[bold green]CONNECTED[/]" if state.connected else "[bold red]DISCONNECTED[/]"
    return Panel(
        f"[bold]Tabula Rasa Agent Monitor[/bold]  │  {status}  │  [dim]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]",
        style="bold cyan",
        box=box.HORIZONTALS,
    )

def render_log() -> Panel:
    lines = Text()
    for line in state.log:
        lines.append_text(line)
        lines.append("\n")
    return Panel(lines, title="[bold]Event Stream[/]", border_style="cyan", box=box.ROUNDED)

def render_sidebar() -> Panel:
    grid = Table.grid(padding=(0, 1))
    grid.add_column(style="dim")
    grid.add_column(style="bold")
    rows = [
        ("Cycles",      str(state.stats["cycles"])),
        ("Tool calls",  str(state.stats["tool_calls"])),
        ("Errors",      str(state.stats["errors"])),
        ("Last model",  state.stats["last_model"]),
        ("Last tool",   state.stats["last_tool"]),
        ("Last action", state.stats["last_action"]),
    ]
    for label, value in rows:
        grid.add_row(label, value)
    return Panel(grid, title="[bold]Stats[/]", border_style="magenta", box=box.ROUNDED)

def render_footer() -> Panel:
    return Panel(
        "[dim]q[/dim] Quit  │  Events scroll automatically  │  Open [bold]chat_client.py[/bold] to talk to the agent",
        box=box.HORIZONTALS,
        style="dim",
    )

def update_stats(evt: dict):
    etype = evt.get("type")
    if etype == "cycle_start":
        state.stats["cycles"] += 1
        state.stats["last_action"] = "Running cycle"
    elif etype == "tool_call":
        state.stats["tool_calls"] += 1
        state.stats["last_tool"] = evt.get("tool", "?")
        state.stats["last_action"] = f"Tool: {evt.get('tool','?')}"
    elif etype == "error":
        state.stats["errors"] += 1
    elif etype == "model_call":
        state.stats["last_model"] = evt.get("model", "?")
    elif etype == "chat_in":
        state.stats["last_action"] = "Chatting"

async def receive_events(layout: Layout, live: Live):
    while True:
        try:
            state.connected = False
            async with websockets.connect(MONITOR_URI) as ws:
                state.connected = True
                state.log.append(Text("── Connected to agent monitor ──", style="bold green"))
                async for raw in ws:
                    evt = json.loads(raw)
                    state.log.append(format_event(evt))
                    update_stats(evt)
                    layout["header"].update(render_header())
                    layout["log"].update(render_log())
                    layout["sidebar"].update(render_sidebar())
                    live.refresh()
        except Exception as e:
            state.connected = False
            state.log.append(Text(f"── Disconnected: {e} – reconnecting in 3s ──", style="bold red"))
            await asyncio.sleep(3)

async def keyboard_listener():
    """Allow pressing q to quit."""
    loop = asyncio.get_event_loop()
    while True:
        char = await loop.run_in_executor(None, sys.stdin.read, 1)
        if char.lower() == "q":
            sys.exit(0)

async def main():
    layout = build_layout()
    layout["header"].update(render_header())
    layout["log"].update(render_log())
    layout["sidebar"].update(render_sidebar())
    layout["footer"].update(render_footer())

    with Live(layout, screen=True, refresh_per_second=4) as live:
        await asyncio.gather(
            receive_events(layout, live),
            keyboard_listener(),
        )

if __name__ == "__main__":
    console.print("[bold cyan]Tabula Rasa Monitor — connecting to agent...[/bold cyan]")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[dim]Monitor closed.[/dim]")
