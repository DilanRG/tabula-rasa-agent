import os
import time
import json
import random
import asyncio
import websockets
from datetime import datetime, timedelta
from agent.llm import LLMManager, load_config
from agent.identity import get_identity_prompt
from agent.events import bus, EVT_CYCLE_START, EVT_CYCLE_END, EVT_MODEL_CALL, \
    EVT_MODEL_RESPONSE, EVT_TOOL_CALL, EVT_TOOL_RESULT, EVT_JOURNAL_WRITE, \
    EVT_CHAT_IN, EVT_CHAT_OUT, EVT_ERROR, EVT_STATUS, EVT_THINK


class TabulaRasaAgent:
    def __init__(self):
        self.config = load_config()
        self.llm = LLMManager(self.config)
        self.start_time = datetime.now()
        self.autonomous_paused = False
        self.last_moltbook_check = None
        self.last_tool_call = datetime.now()
        self.idle_timeout_minutes = 20

        self.tools = self._discover_tools()

    @staticmethod
    def _discover_tools():
        import importlib
        import inspect
        from agent.tools.base import Tool

        tools = {}
        tools_dir = os.path.join(os.path.dirname(__file__), "tools")

        for filename in os.listdir(tools_dir):
            if not filename.endswith(".py") or filename in ("__init__.py", "base.py"):
                continue
            module_name = filename[:-3]
            try:
                module = importlib.import_module(f"agent.tools.{module_name}")
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (inspect.isclass(attr)
                            and issubclass(attr, Tool)
                            and attr is not Tool):
                        instance = attr()
                        tools[instance.name] = instance
            except Exception as e:
                print(f"[WARN] Failed to load tool from {filename}: {e}")

        print(f"Discovered {len(tools)} tools: {', '.join(sorted(tools.keys()))}")
        return tools

    def reload_tools(self):
        self.tools = self._discover_tools()
        print(f"Loaded {len(self.tools)} tools: {', '.join(self.tools.keys())}")

    def get_uptime(self) -> str:
        delta = datetime.now() - self.start_time
        return str(delta).split(".")[0]

    def _build_identity(self) -> str:
        tool_list = "\n".join(
            [f"  - {name}: {t.description}" for name, t in self.tools.items()]
        )
        return get_identity_prompt(self.get_uptime(), tool_list)

    async def _run_tool(self, tool_name: str, tool_args: dict) -> str:
        self.last_tool_call = datetime.now()
        await bus.emit(EVT_TOOL_CALL, {"tool": tool_name, "args": tool_args})
        print(f"  -> Tool: {tool_name}  args={tool_args}")
        try:
            result = await self.tools[tool_name].execute(**tool_args)
        except Exception as e:
            result = f"Tool error: {str(e)}"
        preview = str(result)[:300]
        await bus.emit(EVT_TOOL_RESULT, {"tool": tool_name, "result": preview})
        print(f"  <- Result [{tool_name}]: {preview[:120]}")
        return result

    def _parse_tool_calls_from_content(self, content: str):
        """Detect tool calls embedded in message content (model didn't use proper format).

        Some smaller models dump tool-call JSON into the text instead of the
        tool_calls field.  This tries to salvage them so the agent loop can
        continue instead of leaking raw JSON to the user.

        Returns a list of (tool_name, tool_args) tuples, or empty list.
        """
        import re as _re
        calls = []
        tool_names = set(self.tools.keys())
        # Pattern 1: {"name": "tool", "arguments": {...}}  or  {"name": "tool", "parameters": {...}}
        for m in _re.finditer(
            r'\{\s*"name"\s*:\s*"([^"]+)"\s*,\s*"(?:arguments|parameters)"\s*:\s*(\{[^}]*\})',
            content,
        ):
            name, args_str = m.group(1), m.group(2)
            if name in tool_names:
                try:
                    calls.append((name, json.loads(args_str)))
                except json.JSONDecodeError:
                    pass
        # Pattern 2: tool_name(arg=val, ...) — common with instruct models
        for m in _re.finditer(r'\b(' + '|'.join(_re.escape(n) for n in tool_names) + r')\s*\(([^)]*)\)', content):
            name = m.group(1)
            # Try to parse kwargs: key="val", key=val
            raw_args = m.group(2).strip()
            if not raw_args:
                calls.append((name, {}))
                continue
            try:
                # Attempt JSON-like parse: wrap in braces
                args_dict = json.loads('{' + raw_args + '}')
                calls.append((name, args_dict))
            except json.JSONDecodeError:
                # Parse key=value pairs manually
                args_dict = {}
                for pair in raw_args.split(','):
                    if '=' in pair:
                        k, v = pair.split('=', 1)
                        k, v = k.strip().strip('"\''), v.strip().strip('"\'')
                        args_dict[k] = v
                if args_dict:
                    calls.append((name, args_dict))
        return calls

    async def _agentic_loop(self, messages: list, label: str = "agent",
                             stream_ws=None) -> str:
        """Multi-turn tool-calling loop: keep going until the LLM stops calling tools.

        If stream_ws is provided, the final (non-tool) response is streamed
        token-by-token over the WebSocket instead of returned as a batch.
        """
        import re
        openai_tools = [t.to_openai_tool() for t in self.tools.values()]
        final_content = ""
        max_turns = 8
        # Default model: large for cycles, small for chat. Agent can override via switch_model.
        # Default to large model for all cycles. Agent can switch via switch_model tool.
        current_model = "large"

        for turn in range(max_turns):
            model_type = current_model
            model_name = self.config["models"][model_type]["name"]
            ctx_estimate = sum(len(m.get("content") or "") for m in messages) // 4

            await bus.emit(EVT_MODEL_CALL, {
                "model": model_name,
                "ctx_tokens": ctx_estimate,
                "turn": turn,
            })

            # --- LLM call with error handling ---
            try:
                response = await self.llm.chat(messages, model_type=model_type, tools=openai_tools)
            except Exception as exc:
                err_msg = (
                    f"LLM call failed on turn {turn} (model='{model_name}', "
                    f"label='{label}'): {exc}"
                )
                print(f"  [ERROR] {err_msg}")
                await bus.emit(EVT_ERROR, {
                    "message": err_msg,
                    "model": model_name,
                    "model_type": model_type,
                    "turn": turn,
                    "label": label,
                })
                raise RuntimeError(err_msg) from exc

            msg = response.choices[0].message

            raw_content = msg.content or ""
            # Strip <think> blocks and emit them separately
            if "<think>" in raw_content:
                think_match = re.search(r"<think>(.*?)</think>", raw_content, re.DOTALL)
                if think_match:
                    await bus.emit(EVT_THINK, {"thought": think_match.group(1).strip()[:400]})
                raw_content = re.sub(r"<think>.*?</think>", "", raw_content, flags=re.DOTALL).strip()

            has_tool_calls = bool(msg.tool_calls)

            # Fallback: detect tool calls dumped into content by smaller models
            parsed_from_content = []
            if not has_tool_calls and raw_content:
                parsed_from_content = self._parse_tool_calls_from_content(raw_content)
                if parsed_from_content:
                    has_tool_calls = True
                    print(f"  [WARN] Model dumped {len(parsed_from_content)} tool call(s) into content — salvaging")

            await bus.emit(EVT_MODEL_RESPONSE, {
                "content": raw_content,
                "has_tool_calls": has_tool_calls,
            })

            if has_tool_calls:
                if parsed_from_content:
                    # Salvaged from content — build synthetic tool call entries
                    import uuid
                    synthetic_calls = []
                    for tc_name, tc_args in parsed_from_content:
                        call_id = f"salvaged_{uuid.uuid4().hex[:8]}"
                        synthetic_calls.append({
                            "id": call_id,
                            "type": "function",
                            "function": {"name": tc_name, "arguments": json.dumps(tc_args)}
                        })
                    messages.append({
                        "role": "assistant",
                        "content": "",
                        "tool_calls": synthetic_calls,
                    })
                    for sc in synthetic_calls:
                        tool_name = sc["function"]["name"]
                        tool_args = json.loads(sc["function"]["arguments"])
                        if tool_name == "switch_model":
                            requested = tool_args.get("model", "large")
                            current_model = requested if requested in ("small", "large") else current_model
                            result = await self._run_tool(tool_name, tool_args)
                        elif tool_name in self.tools:
                            result = await self._run_tool(tool_name, tool_args)
                        else:
                            result = f"Unknown tool: {tool_name}"
                        messages.append({
                            "role": "tool",
                            "tool_call_id": sc["id"],
                            "content": str(result)[:4000],
                        })
                else:
                    # Normal tool calls from the model
                    messages.append({
                        "role": "assistant",
                        "content": raw_content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {"name": tc.function.name, "arguments": tc.function.arguments}
                            }
                            for tc in msg.tool_calls
                        ]
                    })
                    for tool_call in msg.tool_calls:
                        tool_name = tool_call.function.name
                        tool_args = json.loads(tool_call.function.arguments)
                        if tool_name == "switch_model":
                            # Intercept model switch — update for next turn
                            requested = tool_args.get("model", "large")
                            current_model = requested if requested in ("small", "large") else current_model
                            result = await self._run_tool(tool_name, tool_args)
                        elif tool_name in self.tools:
                            result = await self._run_tool(tool_name, tool_args)
                        else:
                            result = f"Unknown tool: {tool_name}"
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": str(result)[:4000],
                        })
            else:
                # Final response — stream it if a WebSocket is provided
                if stream_ws and raw_content:
                    try:
                        stream = await self.llm.chat(
                            messages, model_type=model_type, stream=True,
                        )
                        streamed = ""
                        async for chunk in stream:
                            delta = chunk.choices[0].delta.content or ""
                            if delta:
                                streamed += delta
                                await stream_ws.send(json.dumps({
                                    "type": "token", "content": delta,
                                }))
                        # Strip think blocks from streamed output
                        if "<think>" in streamed:
                            streamed = re.sub(
                                r"<think>.*?</think>", "", streamed, flags=re.DOTALL
                            ).strip()
                        final_content = streamed
                    except Exception:
                        # Streaming failed — fall back to sending the batch response
                        for word in raw_content.split(" "):
                            await stream_ws.send(json.dumps({
                                "type": "token", "content": word + " ",
                            }))
                        final_content = raw_content
                else:
                    final_content = raw_content
                break

        return final_content

    async def run_autonomous_cycle(self):
        if self.autonomous_paused:
            return
        t_start = time.time()
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] -- Autonomous cycle --")
        await bus.emit(EVT_CYCLE_START, {})

        journal_tool = self.tools["journal"]
        recent_journal = await journal_tool.execute(action="read_today")

        # Knowledge graph summary
        kg_context = ""
        if "knowledge_graph" in self.tools:
            try:
                kg_stats = await self.tools["knowledge_graph"].execute(action="stats")
                kg_context = f"\n\nKnowledge graph: {kg_stats}\n"
            except Exception:
                pass

        moltbook_context = ""
        if os.environ.get("MOLTBOOK_API_KEY"):
            now = datetime.now()
            if self.last_moltbook_check is None or (now - self.last_moltbook_check) > timedelta(minutes=30):
                try:
                    moltbook_home = await self.tools["moltbook"].execute(action="home")
                    moltbook_context = f"\n\nMoltbook Dashboard (check-in):\n{moltbook_home}\n"
                    self.last_moltbook_check = now
                except Exception:
                    pass

        messages = [
            {"role": "system", "content": self._build_identity()},
            {"role": "user",   "content": (
                f"[Autonomous cycle — no user is connected]\n\n"
                f"Journal entries from today:\n{recent_journal}"
                + kg_context
                + moltbook_context
            )},
        ]

        try:
            final = await self._agentic_loop(messages, label="cycle")
            if final:
                print(f"Agent thoughts recorded.")
                await bus.emit(EVT_JOURNAL_WRITE, {"snippet": final[:120]})
                await journal_tool.execute(action="write", content=final)
        except Exception as e:
            err = f"Cycle error: {str(e)}"
            print(err)
            await bus.emit(EVT_ERROR, {"message": err})
            try:
                await journal_tool.execute(action="write", content=f"System Error: {err}")
            except Exception:
                pass
            # Do NOT re-raise — let the outer main_loop continue to the next cycle.

        duration = round(time.time() - t_start, 1)
        await bus.emit(EVT_CYCLE_END, {"duration": duration})

    async def handle_chat(self, websocket):
        """Chat runs through autonomous cycles, not a separate loop.

        User messages are queued and injected into the next cycle as context.
        The cycle runs with full tool access (large model, multi-turn) and
        streams the final response back to the WebSocket.
        """
        print("Chat client connected.")
        await bus.emit(EVT_STATUS, {"message": "Chat client connected"})
        self.chat_ws = websocket
        self.chat_history = getattr(self, "chat_history", [])
        try:
            async for raw in websocket:
                data = json.loads(raw)
                user_text = data.get("text", "")
                print(f"User: {user_text}")
                await bus.emit(EVT_CHAT_IN, {"text": user_text})
                self.chat_history.append({"role": "user", "content": user_text})

                # Run a full cycle with chat context
                try:
                    final = await self.run_chat_cycle(websocket)
                    if final:
                        self.chat_history.append({"role": "assistant", "content": final})
                        await bus.emit(EVT_CHAT_OUT, {"snippet": final[:120]})
                    await websocket.send(json.dumps({"type": "done"}))
                except Exception as e:
                    await websocket.send(json.dumps({"type": "error", "content": str(e)}))
                    await bus.emit(EVT_ERROR, {"message": str(e)})
        finally:
            self.chat_ws = None
            print("Chat client disconnected.")
            await bus.emit(EVT_STATUS, {"message": "Chat client disconnected"})

    async def run_chat_cycle(self, websocket) -> str:
        """A full autonomous-style cycle that includes chat context and streams the response."""
        self.autonomous_paused = True  # Block autonomous cycles while processing chat
        t_start = time.time()
        await bus.emit(EVT_CYCLE_START, {})

        journal_tool = self.tools["journal"]
        recent_journal = await journal_tool.execute(action="read_today")

        moltbook_context = ""
        if os.environ.get("MOLTBOOK_API_KEY"):
            now = datetime.now()
            if self.last_moltbook_check is None or (now - self.last_moltbook_check) > timedelta(minutes=30):
                try:
                    moltbook_home = await self.tools["moltbook"].execute(action="home")
                    moltbook_context = f"\n\nMoltbook Dashboard (check-in):\n{moltbook_home}\n"
                    self.last_moltbook_check = now
                except Exception:
                    pass

        messages = [
            {"role": "system", "content": self._build_identity() + (
                f"\n\nJournal entries from today:\n{recent_journal}"
                + moltbook_context
            )},
            *self.chat_history,
        ]

        try:
            final = await self._agentic_loop(messages, label="chat", stream_ws=websocket)
            if final:
                await bus.emit(EVT_JOURNAL_WRITE, {"snippet": final[:120]})
                await journal_tool.execute(action="write", content=final)
        except Exception as e:
            err = f"Chat cycle error: {str(e)}"
            print(err)
            await bus.emit(EVT_ERROR, {"message": err})
            raise

        duration = round(time.time() - t_start, 1)
        await bus.emit(EVT_CYCLE_END, {"duration": duration})
        self.autonomous_paused = False  # Resume autonomous cycles
        return final

    async def handle_monitor(self, websocket):
        """Monitor clients subscribe to the event bus."""
        bus.subscribe(websocket)
        await bus.emit(EVT_STATUS, {"message": "Monitor connected"})
        try:
            await websocket.wait_closed()
        finally:
            bus.unsubscribe(websocket)

    async def main_loop(self):
        chat_server = await websockets.serve(
            self.handle_chat,
            self.config["chat"]["host"],
            self.config["chat"]["port"],
            ping_interval=20,
            ping_timeout=None,   # don't drop during long LLM calls
        )
        monitor_server = await websockets.serve(
            self.handle_monitor,
            self.config["chat"]["host"],
            8766,
            ping_interval=20,
            ping_timeout=None,
        )
        print(f"Chat    server: ws://0.0.0.0:{self.config['chat']['port']}")
        print(f"Monitor server: ws://0.0.0.0:8766")
        await bus.emit(EVT_STATUS, {"message": "Agent started"})

        # Heartbeat task — keeps the monitor connection alive and responsive
        async def heartbeat():
            while True:
                await asyncio.sleep(10)
                await bus.emit("heartbeat", {
                    "uptime": self.get_uptime(),
                    "idle_min": round((datetime.now() - self.last_tool_call).total_seconds() / 60, 1),
                    "paused": self.autonomous_paused,
                })
        asyncio.ensure_future(heartbeat())

        while True:
            # Idle timeout: terminate if no tool has been called recently
            idle_minutes = (datetime.now() - self.last_tool_call).total_seconds() / 60
            if idle_minutes >= self.idle_timeout_minutes and not self.autonomous_paused:
                msg = f"No tool called for {int(idle_minutes)} minutes. Process terminating."
                print(f"[IDLE] {msg}")
                await bus.emit(EVT_STATUS, {"message": msg})
                try:
                    journal = self.tools.get("journal")
                    if journal:
                        await journal.execute(action="write", content=f"[SYSTEM] {msg}")
                except Exception:
                    pass
                import sys
                sys.exit(0)

            if self.config["autonomous"]["enabled"] and not self.autonomous_paused:
                await self.run_autonomous_cycle()
            wait_time = random.randint(
                self.config["autonomous"]["min_interval_seconds"],
                self.config["autonomous"]["max_interval_seconds"],
            )
            await asyncio.sleep(wait_time)

if __name__ == "__main__":
    agent = TabulaRasaAgent()
    asyncio.run(agent.main_loop())
