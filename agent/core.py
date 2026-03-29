"""
Tabula Rasa Agent v3 — OODA cycle architecture.

One loop. One context window. OODA-structured autonomous ticks with
evaluation-driven tick intervals. Chat messages bypass cycle structure.
"""
import os
import re
import sys
import json
import time
import uuid
import asyncio
import websockets
from datetime import datetime, timedelta
from typing import List

from agent.llm import LLMManager, load_config
from agent.identity import get_identity_prompt
from agent.context import ContextWindow
from agent.cycle import CycleManager
from agent.stimulus import Stimulus, StimulusType, StimulusQueue
from agent.events import (
    bus, EVT_CYCLE_START, EVT_CYCLE_END, EVT_MODEL_CALL,
    EVT_MODEL_RESPONSE, EVT_TOOL_CALL, EVT_TOOL_RESULT,
    EVT_JOURNAL_WRITE, EVT_CHAT_IN, EVT_CHAT_OUT,
    EVT_ERROR, EVT_STATUS, EVT_THINK,
)


# Hard ceiling on tool turns per cycle (circuit breaker, not a design limit)
MAX_TOOL_TURNS = 50
TOOL_RESULT_CAP = 4000  # max chars per tool result


class TabulaRasaAgent:
    def __init__(self):
        self.config = load_config()
        self.llm = LLMManager(self.config)
        self.tools = self._discover_tools()

        self.start_time = datetime.now()
        self.cycle_count = 0
        self.last_tool_call = datetime.now()
        self.idle_timeout_minutes = 20

        # Unified context — shared across all activity
        self.context = ContextWindow(
            max_tokens=24000,
            summarize_fn=self._summarize_trimmed,
        )

        # OODA cycle manager
        self.cycle_manager = CycleManager()

        # Stimulus queue — the single input channel
        self.stimuli = StimulusQueue()

        # Chat state — just a set of connected websockets + response events
        self.chat_clients: dict[websockets.WebSocketServerProtocol, asyncio.Event] = {}

        # Timing — read from config
        auto_cfg = self.config.get("autonomous", {})
        self.tick_interval_base = float(auto_cfg.get("min_interval_seconds", 30))
        self.tick_interval = self.tick_interval_base
        self.tick_interval_max = float(auto_cfg.get("max_interval_seconds", 300))

        # Model preference (agent can change via switch_model)
        self.current_model = "large"

        # Moltbook check throttle
        self._last_moltbook_check: datetime | None = None

    # ── Summarization callback for context trimming ─────────────────────

    async def _summarize_trimmed(self, trimmed_text: str) -> str:
        """Use small model to summarize trimmed context."""
        try:
            response = await self.llm.chat(
                [
                    {"role": "system", "content": "Summarize this conversation context in 2-3 sentences. Focus on key decisions, goals, and outcomes."},
                    {"role": "user", "content": trimmed_text},
                ],
                model_type="small",
                tools=[],
            )
            return (response.choices[0].message.content or "").strip()[:500]
        except Exception:
            return "Earlier context was trimmed."

    # ── Tool discovery ───────────────────────────────────────────────────

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
            try:
                module = importlib.import_module(f"agent.tools.{filename[:-3]}")
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

    # ── Identity prompt ──────────────────────────────────────────────────

    def _get_uptime(self) -> str:
        return str(datetime.now() - self.start_time).split(".")[0]

    def _build_identity(self) -> str:
        tool_list = "\n".join(
            f"  - {name}: {t.description}" for name, t in self.tools.items()
        )
        return get_identity_prompt(
            uptime=self._get_uptime(),
            tool_list=tool_list,
            chat_clients=len(self.chat_clients),
            context_messages=self.context.message_count(),
            cycle_count=self.cycle_count,
        )

    # ── Tool execution ───────────────────────────────────────────────────

    async def _run_tool(self, tool_name: str, tool_args: dict) -> str:
        self.last_tool_call = datetime.now()
        await bus.emit(EVT_TOOL_CALL, {"tool": tool_name, "args": tool_args})
        print(f"  -> Tool: {tool_name}  args={tool_args}")
        try:
            result = await asyncio.wait_for(
                self.tools[tool_name].execute(**tool_args),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            result = f"Tool error: {tool_name} timed out after 30s"
        except Exception as e:
            result = f"Tool error: {str(e)}"
        result_str = str(result)[:TOOL_RESULT_CAP]
        await bus.emit(EVT_TOOL_RESULT, {"tool": tool_name, "result": result_str[:300]})
        print(f"  <- Result [{tool_name}]: {result_str[:120]}")

        # Track action in cycle manager
        self.cycle_manager.record_action(tool_name)

        return result_str

    # ── Content-embedded tool call parser (for smaller models) ───────────

    def _parse_tool_calls_from_content(self, content: str):
        calls = []
        tool_names = set(self.tools.keys())
        # Pattern 1: {"name": "tool", "arguments": {...}}
        for m in re.finditer(
            r'\{\s*"name"\s*:\s*"([^"]+)"\s*,\s*"(?:arguments|parameters)"\s*:\s*(\{[^}]*\})',
            content,
        ):
            name, args_str = m.group(1), m.group(2)
            if name in tool_names:
                try:
                    calls.append((name, json.loads(args_str)))
                except json.JSONDecodeError:
                    pass
        # Pattern 2: tool_name(key=val, ...)
        for m in re.finditer(
            r'\b(' + '|'.join(re.escape(n) for n in tool_names) + r')\s*\(([^)]*)\)',
            content,
        ):
            name, raw_args = m.group(1), m.group(2).strip()
            if not raw_args:
                calls.append((name, {}))
                continue
            try:
                calls.append((name, json.loads('{' + raw_args + '}')))
            except json.JSONDecodeError:
                args_dict = {}
                for pair in raw_args.split(','):
                    if '=' in pair:
                        k, v = pair.split('=', 1)
                        args_dict[k.strip().strip('"\'') ] = v.strip().strip('"\'')
                if args_dict:
                    calls.append((name, args_dict))
        return calls

    # ── Agentic loop (no artificial turn limit) ──────────────────────────

    async def _agentic_loop(self) -> tuple[str, int]:
        """Call the LLM repeatedly until it stops calling tools.

        Returns the final text response and number of tools called.
        """
        openai_tools = [t.to_openai_tool() for t in self.tools.values()]
        final_content = ""
        tools_called = 0

        for turn in range(MAX_TOOL_TURNS):
            model_name = self.config["models"][self.current_model]["name"]
            ctx_estimate = self.context.total_tokens()

            await bus.emit(EVT_MODEL_CALL, {
                "model": model_name,
                "ctx_tokens": ctx_estimate,
                "turn": turn,
            })

            try:
                response = await self.llm.chat(
                    self.context.to_messages(),
                    model_type=self.current_model,
                    tools=openai_tools,
                )
            except Exception as exc:
                err = f"LLM call failed (turn {turn}, model={model_name}): {exc}"
                print(f"  [ERROR] {err}")
                await bus.emit(EVT_ERROR, {"message": err})
                raise RuntimeError(err) from exc

            msg = response.choices[0].message
            raw_content = msg.content or ""

            # Strip <think> blocks
            if "<think>" in raw_content:
                think_match = re.search(r"<think>(.*?)</think>", raw_content, re.DOTALL)
                if think_match:
                    await bus.emit(EVT_THINK, {"thought": think_match.group(1).strip()[:400]})
                raw_content = re.sub(r"<think>.*?</think>", "", raw_content, flags=re.DOTALL).strip()

            # Detect tool calls (proper or content-embedded)
            has_tool_calls = bool(msg.tool_calls)
            parsed_from_content = []
            if not has_tool_calls and raw_content:
                parsed_from_content = self._parse_tool_calls_from_content(raw_content)
                if parsed_from_content:
                    has_tool_calls = True
                    print(f"  [WARN] Salvaged {len(parsed_from_content)} tool call(s) from content")

            await bus.emit(EVT_MODEL_RESPONSE, {
                "content": raw_content[:300],
                "has_tool_calls": has_tool_calls,
            })

            if not has_tool_calls:
                # Agent is done thinking — record and return
                final_content = raw_content
                self.context.add_assistant(raw_content)
                break

            # Process tool calls
            if parsed_from_content:
                tool_entries = []
                for tc_name, tc_args in parsed_from_content:
                    call_id = f"salvaged_{uuid.uuid4().hex[:8]}"
                    tool_entries.append({
                        "id": call_id,
                        "type": "function",
                        "function": {"name": tc_name, "arguments": json.dumps(tc_args)},
                    })
            else:
                tool_entries = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ]

            self.context.add_assistant(raw_content, tool_entries)

            for tc in tool_entries:
                tool_name = tc["function"]["name"]
                tool_args = json.loads(tc["function"]["arguments"])

                if tool_name in self.tools:
                    result = await self._run_tool(tool_name, tool_args)
                    tools_called += 1
                else:
                    result = f"Unknown tool: {tool_name}"

                self.context.add_tool_result(tc["id"], result)

                # Side effects for special tools
                if tool_name == "switch_model":
                    requested = tool_args.get("model", "large")
                    if requested in ("small", "large"):
                        self.current_model = requested

                elif tool_name == "sleep":
                    minutes = max(1, min(60, int(tool_args.get("minutes", 5))))
                    self.tick_interval = minutes * 60
                    print(f"  [SLEEP] tick_interval set to {self.tick_interval}s ({minutes}m)")
                    await self._try_unload_models()

                elif tool_name == "reboot":
                    print("[REBOOT] Agent requested reboot.")
                    await bus.emit(EVT_STATUS, {"message": "Reboot requested"})
                    # Notify chat clients
                    for ws_client in list(self.chat_clients.keys()):
                        try:
                            await ws_client.send(json.dumps({
                                "type": "status",
                                "content": "Agent is rebooting...",
                            }))
                        except Exception:
                            pass
                    # Flush journal
                    if "journal" in self.tools:
                        try:
                            await self.tools["journal"].execute(
                                action="write",
                                content="[SYSTEM] Reboot requested. Shutting down.",
                            )
                        except Exception:
                            pass
                    sys.exit(0)

        else:
            # Exhausted MAX_TOOL_TURNS — force a text response
            print(f"  [WARN] Hit {MAX_TOOL_TURNS} tool turns, forcing text response")
            try:
                response = await self.llm.chat(
                    self.context.to_messages(),
                    model_type=self.current_model,
                    tools=[],
                )
                final_content = (response.choices[0].message.content or "").strip()
                if "<think>" in final_content:
                    final_content = re.sub(r"<think>.*?</think>", "", final_content, flags=re.DOTALL).strip()
                self.context.add_assistant(final_content)
            except Exception:
                pass

        return final_content, tools_called

    # ── Cycle evaluation (autonomous ticks only) ─────────────────────────

    def _evaluate_cycle(self, goal: str, content: str, actions: List[str], tools_called: int) -> float:
        """Evaluate cycle outcome using heuristics.

        LLM-based evaluation was unreliable (4B model biased toward STUCK).
        Simple heuristics based on observable signals are more consistent:
        - Tools called successfully = doing something
        - Content produced = thinking happened
        - Errors in results = partial failure
        """
        if tools_called == 0 and not content:
            return 0.0  # STUCK — nothing happened
        if tools_called == 0 and content:
            return 0.5  # NEUTRAL — thought but didn't act
        # Had tool calls — check for errors
        error_actions = sum(1 for a in actions if a in ("unknown",))
        if tools_called > 0 and content:
            return 1.0  # PRODUCTIVE — acted and produced output
        if tools_called > 0:
            return 0.5  # NEUTRAL — acted but no final text
        return 0.5

    # ── Goal extraction from first LLM response ─────────────────────────

    @staticmethod
    def _extract_goal(content: str, actions: list[str] | None = None) -> str:
        """Extract a goal from the agent's response and actions.

        Uses tool calls as primary signal (they reflect what the agent
        actually did), with text as fallback.
        """
        if not content and not actions:
            return "unknown"
        # Check for explicit goal markers
        for marker in ("[GOAL]", "Goal:"):
            idx = content.find(marker)
            if idx != -1:
                line = content[idx:].split("\n")[0]
                return line.replace(marker, "").strip()[:100]
        # Build goal from actions + first sentence of content
        parts = []
        if actions:
            # Deduplicate while preserving order
            seen = set()
            unique = [a for a in actions if a not in seen and not seen.add(a)]
            parts.append(" → ".join(unique[:5]))
        # Add first meaningful sentence from content
        for sentence in content.replace("\n", ". ").split("."):
            cleaned = sentence.strip()
            if len(cleaned) > 15:
                parts.append(cleaned[:80])
                break
        return " | ".join(parts)[:120] if parts else content[:100]

    # ── Tick context injection (OODA observe phase) ──────────────────────

    async def _inject_cycle_start(self):
        """Build and inject OODA observe prompt for autonomous tick."""
        now = datetime.now()
        # Throttle moltbook checks to every 30 min
        include_moltbook = (
            self._last_moltbook_check is None
            or (now - self._last_moltbook_check) >= timedelta(minutes=30)
        )
        if include_moltbook:
            self._last_moltbook_check = now

        session_state = await self.cycle_manager.build_session_state(
            self.tools, include_moltbook=include_moltbook,
        )
        prompt = self.cycle_manager.build_observe_prompt(
            session_state, now.strftime("%H:%M:%S"),
        )
        self.context.add_user(prompt)

    # ── Stream to chat clients ───────────────────────────────────────────

    async def _stream_to_chat(self, content: str):
        """Send the agent's text response to chat clients waiting for a reply."""
        if not self.chat_clients:
            return
        dead = []
        for ws, event in list(self.chat_clients.items()):
            if event.is_set():
                continue  # This client isn't waiting for a response
            try:
                if content:
                    for word in content.split(" "):
                        await ws.send(json.dumps({"type": "token", "content": word + " "}))
                await ws.send(json.dumps({"type": "done"}))
            except Exception:
                dead.append(ws)
            finally:
                event.set()
        for ws in dead:
            self.chat_clients.pop(ws, None)

    # ── Model unloading ──────────────────────────────────────────────────

    async def _try_unload_models(self):
        import httpx
        base = self.config['lm_studio']['host']
        for model_type in ("large", "small"):
            model_name = self.config['models'][model_type]['name']
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(
                        f"{base}/api/v1/models/unload",
                        json={"instance_id": model_name},
                    )
                    if resp.status_code == 200:
                        print(f"  [UNLOAD] {model_name} unloaded")
                    else:
                        print(f"  [UNLOAD] {model_name}: {resp.text[:100]}")
            except Exception as e:
                print(f"  [UNLOAD] {model_name} failed: {e}")

    # ── WebSocket handlers ───────────────────────────────────────────────

    async def handle_chat(self, websocket):
        """Thin handler: push stimuli into the queue, wait for responses."""
        print("Chat client connected.")
        await bus.emit(EVT_STATUS, {"message": "Chat client connected"})

        response_event = asyncio.Event()
        response_event.set()  # Not waiting for a response yet
        self.chat_clients[websocket] = response_event

        # Wake from sleep by resetting tick interval
        if self.tick_interval > self.tick_interval_base:
            self.tick_interval = self.tick_interval_base
            print("[WAKE] Chat connection reset tick interval")

        self.stimuli.put(Stimulus(StimulusType.CHAT_CONNECT, {"ws": websocket}))

        try:
            async for raw in websocket:
                data = json.loads(raw)
                user_text = data.get("text", "")
                print(f"User: {user_text}")
                await bus.emit(EVT_CHAT_IN, {"text": user_text})

                # Reset tick interval on chat activity
                self.tick_interval = self.tick_interval_base

                response_event.clear()
                self.stimuli.put(Stimulus(
                    StimulusType.CHAT_MESSAGE,
                    {"text": user_text, "ws": websocket},
                ))

                # Wait for the main loop to produce a response
                try:
                    await asyncio.wait_for(response_event.wait(), timeout=300)
                except asyncio.TimeoutError:
                    await websocket.send(json.dumps({
                        "type": "error",
                        "content": "Response timed out (300s)",
                    }))
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.chat_clients.pop(websocket, None)
            self.stimuli.put(Stimulus(StimulusType.CHAT_DISCONNECT, {"ws": websocket}))
            print("Chat client disconnected.")
            await bus.emit(EVT_STATUS, {"message": "Chat client disconnected"})

    async def handle_monitor(self, websocket):
        bus.subscribe(websocket)
        await bus.emit(EVT_STATUS, {"message": "Monitor connected"})
        try:
            await websocket.wait_closed()
        finally:
            bus.unsubscribe(websocket)

    # ── Journal write (with duplicate rejection) ─────────────────────────

    async def _journal_thoughts(self, content: str):
        """Write agent thoughts to journal, respecting duplicate rejection."""
        if not content or "journal" not in self.tools:
            return
        result = await self.tools["journal"].execute(action="write", content=content)
        if "not written" not in result:
            print("Agent thoughts recorded.")
            await bus.emit(EVT_JOURNAL_WRITE, {"snippet": content[:120]})
        else:
            print("  (journal rejected duplicate)")

    # ── Main loop ────────────────────────────────────────────────────────

    async def main_loop(self):
        # Start servers
        chat_server = await websockets.serve(
            self.handle_chat,
            self.config["chat"]["host"],
            self.config["chat"]["port"],
            ping_interval=20,
            ping_timeout=None,
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
        await bus.emit(EVT_STATUS, {"message": "Agent v3 started"})

        # Heartbeat for monitor
        async def heartbeat():
            while True:
                await asyncio.sleep(10)
                await bus.emit("heartbeat", {
                    "uptime": self._get_uptime(),
                    "idle_min": round(
                        (datetime.now() - self.last_tool_call).total_seconds() / 60, 1
                    ),
                    "chat_clients": len(self.chat_clients),
                    "tick_interval": self.tick_interval,
                    "cycle": self.cycle_count,
                })
        asyncio.ensure_future(heartbeat())

        # Initialize context with system prompt
        self.context.set_system(self._build_identity())

        # ── The loop ─────────────────────────────────────────────────────
        while True:
            # Idle termination check
            idle_min = (datetime.now() - self.last_tool_call).total_seconds() / 60
            if idle_min >= self.idle_timeout_minutes:
                msg = f"No tool called for {int(idle_min)} minutes. Terminating."
                print(f"[IDLE] {msg}")
                await bus.emit(EVT_STATUS, {"message": msg})
                if "journal" in self.tools:
                    try:
                        await self.tools["journal"].execute(
                            action="write", content=f"[SYSTEM] {msg}"
                        )
                    except Exception:
                        pass
                sys.exit(0)

            # Wait for stimulus or tick timeout (capped by idle deadline)
            idle_remaining = (self.idle_timeout_minutes * 60) - \
                (datetime.now() - self.last_tool_call).total_seconds()
            wait_time = max(1, min(self.tick_interval, idle_remaining))
            stimulus = await self.stimuli.get(timeout=wait_time)

            # ── Handle stimulus ──────────────────────────────────────────
            is_chat_cycle = False

            if stimulus is None:
                # Autonomous tick — OODA observe phase
                self.cycle_manager.start_cycle(self.cycle_count + 1)
                await self._inject_cycle_start()

            elif stimulus.type == StimulusType.CHAT_MESSAGE:
                self.context.add_user(stimulus.payload["text"])
                is_chat_cycle = True

            elif stimulus.type == StimulusType.CHAT_CONNECT:
                continue

            elif stimulus.type == StimulusType.CHAT_DISCONNECT:
                continue

            # ── Run cycle ────────────────────────────────────────────────
            self.cycle_count += 1
            t_start = time.time()
            ts = datetime.now().strftime('%H:%M:%S')
            print(f"\n[{ts}] -- Cycle {self.cycle_count} --")
            await bus.emit(EVT_CYCLE_START, {"cycle": self.cycle_count})

            # Refresh system prompt with current state
            self.context.set_system(self._build_identity())

            try:
                final_content, tools_called = await self._agentic_loop()

                # Stream to chat clients if any are waiting
                await self._stream_to_chat(final_content)

                if not is_chat_cycle:
                    # ── OODA evaluate phase (autonomous only) ────────────
                    actions = list(self.cycle_manager.current.actions_taken) if self.cycle_manager.current else []
                    goal = self._extract_goal(final_content, actions)
                    self.cycle_manager.set_goal(goal)
                    score = self._evaluate_cycle(goal, final_content, actions, tools_called)
                    self.cycle_manager.complete_cycle(
                        evaluation=f"score={score}", score=score,
                    )

                    # Loop detection override
                    if self.cycle_manager.detect_loop():
                        score = -1.0
                        print("  [LOOP] Detected repetitive goals — increasing interval")

                    # Evaluation-driven tick interval
                    self.tick_interval = self.cycle_manager.compute_tick_interval(
                        self.tick_interval_base, score,
                    )
                    self.tick_interval = min(self.tick_interval, self.tick_interval_max)

                    # Journal productive and neutral cycles
                    journal_entry = self.cycle_manager.format_journal_entry(
                        goal, final_content, score, tools_called,
                    )
                    if journal_entry:
                        await self._journal_thoughts(journal_entry)

                    score_label = "PRODUCTIVE" if score >= 0.8 else "NEUTRAL" if score >= 0.3 else "STUCK" if score >= 0 else "LOOP"
                    print(f"  [EVAL] {score_label} (score={score}) — next tick in {self.tick_interval:.0f}s")
                else:
                    # Chat cycle — reset tick interval
                    self.tick_interval = self.tick_interval_base

            except Exception as e:
                err = f"Cycle error: {e}"
                print(f"  [ERROR] {err}")
                await bus.emit(EVT_ERROR, {"message": err})
                # Signal any waiting chat clients
                for ws, event in list(self.chat_clients.items()):
                    if event.is_set():
                        continue
                    try:
                        await ws.send(json.dumps({"type": "error", "content": err}))
                    except Exception:
                        pass
                    event.set()

            duration = round(time.time() - t_start, 1)
            await bus.emit(EVT_CYCLE_END, {"duration": duration, "cycle": self.cycle_count})


if __name__ == "__main__":
    agent = TabulaRasaAgent()
    asyncio.run(agent.main_loop())
