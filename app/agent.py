import asyncio
import json
import logging
import time

from openai import AsyncOpenAI

from app.config import OPENAI_MODEL
from app.engine import GameEngine
from app.events import Event, EventBus, EventType
from app.prompts import build_system_prompt
from app.tools import _get_tools_for_firm, dispatch_tool_call

logger = logging.getLogger(__name__)


class Agent:
    def __init__(self, firm_id: str, engine: GameEngine, event_bus: EventBus) -> None:
        self.firm_id = firm_id
        self.engine = engine
        self.event_bus = event_bus
        self.client = AsyncOpenAI()
        self.conversation_history: list[dict] = []
        self._running = False
        self._tools = _get_tools_for_firm(firm_id)

    async def run(self) -> None:
        self._running = True
        self.conversation_history = [
            {"role": "system", "content": build_system_prompt(self.firm_id)},
            {
                "role": "user",
                "content": "The game has started. You have 5 minutes. Begin by checking your state, then start trading and producing.",
            },
        ]

        while self._running and self.engine.game_running:
            try:
                await self._step()
            except Exception:
                logger.exception("Agent %s error", self.firm_id)
                await self.event_bus.publish(Event(
                    type=EventType.AGENT_ERROR,
                    firm_id=self.firm_id,
                    data={"error": "agent step failed"},
                    timestamp=time.time(),
                ))
                await asyncio.sleep(2)

    async def _step(self) -> None:
        await self.event_bus.publish(Event(
            type=EventType.AGENT_THINKING,
            firm_id=self.firm_id,
            data={},
            timestamp=time.time(),
        ))

        response = await self.client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=self.conversation_history,
            tools=self._tools,
            tool_choice="auto",
        )

        message = response.choices[0].message

        # Append assistant message to history
        msg_dict: dict = {"role": "assistant"}
        if message.content:
            msg_dict["content"] = message.content
        if message.tool_calls:
            msg_dict["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]
        self.conversation_history.append(msg_dict)

        if message.tool_calls:
            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                try:
                    arguments = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    arguments = {}

                await self.event_bus.publish(Event(
                    type=EventType.AGENT_TOOL_CALL,
                    firm_id=self.firm_id,
                    data={"tool": tool_name, "args": arguments},
                    timestamp=time.time(),
                ))

                result = await dispatch_tool_call(
                    self.engine, self.firm_id, tool_name, arguments
                )

                self.conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })
        else:
            # Model produced text only - nudge it to act
            remaining = self.engine.time_remaining()
            self.conversation_history.append({
                "role": "user",
                "content": f"Time remaining: {remaining:.0f}s. Use your tools to take action.",
            })
            await asyncio.sleep(1)

    def stop(self) -> None:
        self._running = False
