import asyncio
import logging
from enum import Enum
from typing import Any, Callable, Coroutine

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    GAME_STARTED = "game_started"
    GAME_ENDED = "game_ended"

    CASH_CHANGED = "cash_changed"
    INVENTORY_CHANGED = "inventory_changed"
    FACTORY_PURCHASED = "factory_purchased"

    FACTORY_STARTED = "factory_started"
    FACTORY_COMPLETED = "factory_completed"

    CONTRACT_SENT = "contract_sent"
    CONTRACT_ACCEPTED = "contract_accepted"
    CONTRACT_REJECTED = "contract_rejected"

    MESSAGE_SENT = "message_sent"

    AGENT_THINKING = "agent_thinking"
    AGENT_TOOL_CALL = "agent_tool_call"
    AGENT_ERROR = "agent_error"


class Event(BaseModel):
    type: EventType
    firm_id: str | None = None
    data: dict[str, Any] = {}
    timestamp: float

    class Config:
        arbitrary_types_allowed = True


Callback = Callable[[Event], Coroutine[Any, Any, None]]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[EventType, list[Callback]] = {}
        self._global_subscribers: list[Callback] = []

    def subscribe(self, event_type: EventType, callback: Callback) -> None:
        self._subscribers.setdefault(event_type, []).append(callback)

    def subscribe_all(self, callback: Callback) -> None:
        self._global_subscribers.append(callback)

    async def publish(self, event: Event) -> None:
        callbacks = list(self._global_subscribers)
        callbacks.extend(self._subscribers.get(event.type, []))
        for cb in callbacks:
            try:
                await cb(event)
            except Exception:
                logger.exception("Error in event subscriber for %s", event.type)
