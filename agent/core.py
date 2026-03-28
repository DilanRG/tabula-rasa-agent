import os
import json
import asyncio
import httpx
import websockets
from datetime import datetime
from agent.llm import LLMManager, load_config
from agent.identity import get_identity_prompt
# Import tools
from agent.tools.journal import JournalTool
from agent.tools.web_search import WebSearchTool
from agent.tools.web_read import WebReadTool
from agent.tools.clock import ClockTool
from agent.tools.self_modify import SelfModifyTool
from agent.tools.git import GitTool
from agent.tools.moltbook import MoltbookTool
from agent.tools.reboot import RebootTool

class TabulaRasaAgent:
    def __init__(self):
        self.config = load_config()
        self.llm = LLMManager(self.config)
        self.start_time = datetime.now()
        self.autonomous_paused = False
        
        # Initialize tools
        self.tools = {
            "journal": JournalTool(),
            "web_search": WebSearchTool(),
            "web_read": WebReadTool(),
            "clock": ClockTool(),
            "self_modify": SelfModifyTool(),
            "git": GitTool(),
            "moltbook": MoltbookTool(),
            "reboot": RebootTool()
        }

    def get_uptime(self) -> str:
        delta = datetime.now() - self.start_time
        return str(delta).split(".")[0]

    async def run_autonomous_cycle(self):
        if self.autonomous_paused: return
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting autonomous cycle...")
        
        journal_tool = self.tools['journal']
        recent_journal = await journal_tool.execute(action="read_today")
        
        messages = [
            {"role": "system", "content": get_identity_prompt(self.get_uptime())},
            {"role": "user", "content": f"Recent Journal Entries:\n{recent_journal}\n\nWhat would you like to do? If you want to use a tool, specify it. Otherwise, you can just think or take notes."}
        ]
        
        openai_tools = [t.to_openai_tool() for t in self.tools.values()]
        
        try:
            # Using Large model for autonomous decisions as per plan
            response = await self.llm.chat(messages, model_type="large")
            msg = response.choices[0].message
            
            if msg.tool_calls:
                for tool_call in msg.tool_calls:
                    tool_name = tool_call.function.name
                    tool_args = json.loads(tool_call.function.arguments)
                    
                    if tool_name in self.tools:
                        print(f"Executing tool: {tool_name}")
                        result = await self.tools[tool_name].execute(**tool_args)
                        await journal_tool.execute(action="write", content=f"Executed {tool_name}: {result[:500]}...")
                    else:
                        print(f"Tool {tool_name} not found.")
            else:
                content = msg.content
                if content:
                    print(f"Agent thoughts recorded.")
                    await journal_tool.execute(action="write", content=f"Self-reflection: {content}")

        except Exception as e:
            err_msg = f"Cycle error: {str(e)}"
            print(err_msg)
            try:
                await journal_tool.execute(action="write", content=f"System Error in autonomous cycle: {err_msg}")
            except:
                pass

    async def handle_chat(self, websocket):
        print("New chat client connected.")
        async for message in websocket:
            data = json.loads(message)
            user_text = data.get("text")
            print(f"User message: {user_text}")
            
            # Pause autonomous loop while chatting
            self.autonomous_paused = True
            
            journal_tool = self.tools['journal']
            recent_journal = await journal_tool.execute(action="read_today")
            
            messages = [
                {"role": "system", "content": get_identity_prompt(self.get_uptime())},
                {"role": "user", "content": f"Journal Context:\n{recent_journal}\n\nUser: {user_text}"}
            ]
            
            try:
                # Using Small model for chat for speed
                response = await self.llm.chat(messages, model_type="small", stream=True)
                async for chunk in response:
                    if chunk.choices[0].delta.content:
                        await websocket.send(json.dumps({"type": "token", "content": chunk.choices[0].delta.content}))
                await websocket.send(json.dumps({"type": "done"}))
            except Exception as e:
                await websocket.send(json.dumps({"type": "error", "content": str(e)}))
            finally:
                self.autonomous_paused = False

    async def main_loop(self):
        # Start WebSocket server
        server = await websockets.serve(self.handle_chat, self.config['chat']['host'], self.config['chat']['port'])
        print(f"Chat server running on {self.config['chat']['host']}:{self.config['chat']['port']}")

        while True:
            if self.config['autonomous']['enabled'] and not self.autonomous_paused:
                await self.run_autonomous_cycle()
            
            wait_time = self.config['autonomous']['min_interval_seconds']
            await asyncio.sleep(wait_time)

if __name__ == "__main__":
    agent = TabulaRasaAgent()
    asyncio.run(agent.main_loop())
