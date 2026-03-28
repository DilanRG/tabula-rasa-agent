import asyncio
import websockets
import json
import sys
from rich.console import Console
from rich.markdown import Markdown
from rich.live import Live
from rich.panel import Panel

console = Console()

async def chat():
    uri = "ws://localhost:8765"
    # ping_timeout=None disables WebSocket keepalive timeouts so long LLM
    # inference calls don't drop the connection mid-think.
    async with websockets.connect(uri, ping_interval=20, ping_timeout=None) as websocket:
        console.print("[bold green]Connected to Tabula Rasa Agent.[/bold green]")
        console.print("Type your message and press Enter. (Ctrl+C to exit)\n")
        
        while True:
            try:
                # Use asyncio.to_thread to prevent input() from blocking the websocket heartbeat
                user_input = await asyncio.to_thread(input, "You: ")
                if not user_input: continue
                
                await websocket.send(json.dumps({"text": user_input}))
                
                import time
                elapsed = 0
                full_response = ""
                console.print("\n[bold blue]Agent:[/bold blue]")

                with Live(Panel("", title="Thinking... 0s"), refresh_per_second=4) as live:
                    start = time.time()
                    async for message in websocket:
                        data = json.loads(message)
                        elapsed = int(time.time() - start)
                        if data["type"] == "token":
                            full_response += data["content"]
                            live.update(Panel(Markdown(full_response), title=f"Agent ({elapsed}s)"))
                        elif data["type"] == "done":
                            break
                        elif data["type"] == "error":
                            console.print(f"[bold red]Error: {data['content']}[/bold red]")
                            break
                        else:
                            # Keep spinner updated while waiting for first token
                            live.update(Panel("", title=f"Thinking... {elapsed}s"))
                print("\n")
            except KeyboardInterrupt:
                break
            except Exception as e:
                console.print(f"[bold red]Connection error: {e}[/bold red]")
                break

if __name__ == "__main__":
    try:
        asyncio.run(chat())
    except KeyboardInterrupt:
        pass
