"""
monitor.py — Lightweight real-time log viewer for the Tabula Rasa agent.
Connects to the agent's monitor WebSocket and prints colored events as they arrive.

Usage: python monitor.py
       Ctrl+C to quit.
"""
import asyncio
import json
import sys
from datetime import datetime

import websockets
from rich.console import Console
from rich.text import Text

MONITOR_URI = "ws://localhost:8766"
console = Console(highlight=False)

# Colour / icon palette
STYLES = {
    "cycle_start":    ("bold cyan",    ">>>"),
    "cycle_end":      ("bold green",   "<<<"),
    "model_call":     ("bold blue",    "LLM"),
    "model_response": ("blue",         "RES"),
    "tool_call":      ("bold yellow",  "USE"),
    "tool_result":    ("yellow",       "RET"),
    "journal_write":  ("magenta",      "JRN"),
    "chat_in":        ("bold white",   "USR"),
    "chat_out":       ("green",        "BOT"),
    "error":          ("bold red",     "ERR"),
    "think":          ("dim italic",   "..."),
    "status":         ("dim cyan",     "---"),
}


def format_event(evt: dict) -> Text:
    ts = evt.get("ts", "??:??:??")
    etype = evt.get("type", "status")
    style, tag = STYLES.get(etype, ("white", "???"))

    line = Text()
    line.append(f"{ts} ", style="dim")
    line.append(f"[{tag}] ", style=style)

    if etype == "cycle_start":
        line.append("Autonomous cycle started", style=style)
    elif etype == "cycle_end":
        line.append(f"Cycle complete ({evt.get('duration', '?')}s)", style=style)
    elif etype == "model_call":
        model = evt.get("model", "?")
        ctx = evt.get("ctx_tokens", "?")
        line.append(f"{model}", style="bold " + style)
        line.append(f"  ctx={ctx} tok", style="dim")
    elif etype == "model_response":
        content = str(evt.get("content", ""))
        if evt.get("has_tool_calls"):
            line.append("Tool calls pending", style=style)
        else:
            preview = content[:200] + ("..." if len(content) > 200 else "")
            line.append(preview, style="dim white")
    elif etype == "tool_call":
        tool = evt.get("tool", "?")
        args = json.dumps(evt.get("args", {}))
        if len(args) > 120:
            args = args[:120] + "..."
        line.append(f"{tool}", style="bold " + style)
        line.append(f"  {args}", style="dim")
    elif etype == "tool_result":
        tool = evt.get("tool", "?")
        result = str(evt.get("result", ""))
        preview = result[:200] + ("..." if len(result) > 200 else "")
        line.append(f"{tool}: ", style=style)
        line.append(preview, style="dim white")
    elif etype == "journal_write":
        snippet = str(evt.get("snippet", ""))
        line.append(snippet[:160], style="dim white")
    elif etype == "chat_in":
        line.append(str(evt.get("text", "")), style="bold white")
    elif etype == "chat_out":
        snippet = str(evt.get("snippet", ""))
        line.append(snippet[:200], style="dim white")
    elif etype == "think":
        thought = str(evt.get("thought", ""))
        line.append(thought[:200], style="dim italic")
    elif etype == "error":
        line.append(str(evt.get("message", "")), style=style)
    elif etype == "heartbeat":
        return None  # skip heartbeats
    else:
        line.append(str(evt)[:200], style="dim")

    return line


async def monitor():
    while True:
        try:
            console.print("[dim]Connecting...[/dim]")
            async with websockets.connect(MONITOR_URI) as ws:
                console.print("[bold green]Connected to agent monitor[/bold green]")
                async for raw in ws:
                    evt = json.loads(raw)
                    line = format_event(evt)
                    if line is not None:
                        console.print(line)
        except (ConnectionRefusedError, OSError) as e:
            console.print(f"[bold red]Connection failed — retrying in 2s[/bold red]")
            await asyncio.sleep(2)
        except websockets.exceptions.ConnectionClosed:
            console.print(f"[bold red]Disconnected — reconnecting in 1s[/bold red]")
            await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(monitor())
    except KeyboardInterrupt:
        console.print("\n[dim]Monitor closed.[/dim]")
