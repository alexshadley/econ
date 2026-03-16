import asyncio
import json
import logging
import time

from openai import AsyncOpenAI

from app.config import INPUT_PRICE_PER_TOKEN, OPENAI_MODEL, OUTPUT_PRICE_PER_TOKEN
from app.engine import GameEngine
from app.prompts import build_system_prompt
from app.tools import _get_tools_for_firm, dispatch_tool_call

logger = logging.getLogger(__name__)


def _convert_tools_for_responses_api(chat_tools: list[dict]) -> list[dict]:
    """Convert Chat Completions tool format to Responses API format."""
    result = []
    for tool in chat_tools:
        func = tool["function"]
        result.append(
            {
                "type": "function",
                "name": func["name"],
                "description": func.get("description", ""),
                "parameters": func.get(
                    "parameters", {"type": "object", "properties": {}}
                ),
                "strict": False,
            }
        )
    return result


class Agent:
    def __init__(self, firm_id: str, engine: GameEngine) -> None:
        self.firm_id = firm_id
        self.engine = engine
        self.client = AsyncOpenAI()
        self._running = False
        self._tools = _convert_tools_for_responses_api(_get_tools_for_firm(firm_id))
        self._instructions = build_system_prompt(firm_id)
        self._previous_response_id: str | None = None

    async def run(self, resumed: bool = False) -> None:
        self._running = True
        if resumed:
            self._pending_input = [
                {
                    "role": "user",
                    "content": (
                        "This is a resumed game. You have 5 minutes. "
                        "Check your current state first — you may already have cash, "
                        "inventory, and factories from the previous session. "
                        "Then start trading and producing."
                    ),
                }
            ]
        else:
            self._pending_input = [
                {
                    "role": "user",
                    "content": (
                        "The game has started. You have 5 minutes. "
                        "Begin by checking your state, then start trading and producing."
                    ),
                }
            ]

        while self._running and self.engine.game_running:
            await self._step()

    async def _step(self) -> None:
        self.engine.log_activity("agent_thinking", self.firm_id)

        kwargs: dict = {
            "model": OPENAI_MODEL,
            "instructions": self._instructions,
            "input": self._pending_input,
            "tools": self._tools,
            "tool_choice": "auto",
            "reasoning": {"effort": "low", "summary": "detailed"},
        }
        if self._previous_response_id:
            kwargs["previous_response_id"] = self._previous_response_id

        response = await self.client.responses.create(**kwargs)
        self._previous_response_id = response.id
        self._pending_input = []  # clear; server tracks history

        if response.usage:
            cost = (
                response.usage.input_tokens * INPUT_PRICE_PER_TOKEN
                + response.usage.output_tokens * OUTPUT_PRICE_PER_TOKEN
            )
            self.engine.total_api_cost += cost

        # Process output items
        has_tool_calls = False

        for item in response.output:
            if item.type == "reasoning":
                for summary_part in item.summary:
                    self.engine.record_reasoning_summary(
                        self.firm_id, summary_part.text, time.time()
                    )

            elif item.type == "function_call":
                has_tool_calls = True
                tool_name = item.name
                try:
                    arguments = json.loads(item.arguments)
                except json.JSONDecodeError:
                    arguments = {}

                self.engine.log_activity(
                    "agent_tool_call",
                    self.firm_id,
                    {"tool": tool_name, "args": arguments},
                )

                result = await dispatch_tool_call(
                    self.engine, self.firm_id, tool_name, arguments
                )

                self.engine.record_tool_call(
                    self.firm_id, tool_name, arguments, result, time.time()
                )

                # Send tool output in the next request
                self._pending_input.append(
                    {
                        "type": "function_call_output",
                        "call_id": item.call_id,
                        "output": result,
                    }
                )

        if not has_tool_calls:
            # Model produced text only - nudge it to act
            remaining = self.engine.time_remaining()
            self._pending_input.append(
                {
                    "role": "user",
                    "content": f"Time remaining: {remaining:.0f}s. Use your tools to take action.",
                }
            )
            await asyncio.sleep(1)

    def stop(self) -> None:
        self._running = False
