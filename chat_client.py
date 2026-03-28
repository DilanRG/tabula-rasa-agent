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
    async with websockets.connect(uri) as websocket:
        console.print("[bold green]Connected to Tabula Rasa Agent.[/bold green]")
        console.print("Type your message and press Enter. (Ctrl+C to exit)\n")
        
        while True:
            try:
                user_input = input("You: ")
                if not user_input: continue
                
                await websocket.send(json.dumps({"text": user_input}))
                
                full_response = ""
                console.print("\n[bold blue]Agent:[/bold blue]")
                
                with Live(Panel("", title="Thinking..."), refresh_per_second=10) as live:
                    async for message in websocket:
                        data = json.loads(message)
                        if data["type"] == "token":
                            full_response += data["content"]
                            live.update(Panel(Markdown(full_response), title="Agent"))
                        elif data["type"] == "done":
                            break
                        elif data["type"] == "error":
                            console.print(f"[bold red]Error: {data['content']}[/bold red]")
                            break
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
