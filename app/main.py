import asyncio
import logging
import time

from app.agent import Agent
from app.config import FIRM_CONFIGS, GAME_DURATION_SECONDS
from app.engine import GameEngine
from app.events import Event, EventBus, EventType

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("app")


async def log_event(event: Event) -> None:
    firm = event.firm_id or "game"
    match event.type:
        case EventType.AGENT_TOOL_CALL:
            tool = event.data.get("tool", "?")
            args = event.data.get("args", {})
            logger.info("[%s] tool: %s(%s)", firm, tool, args)
        case EventType.FACTORY_STARTED:
            count = event.data.get("count")
            ft = event.data.get("factory_type")
            cost = event.data.get("cost")
            logger.info("[%s] started %d %s factories (cost $%s)", firm, count, ft, cost)
        case EventType.FACTORY_COMPLETED:
            count = event.data.get("count")
            output = event.data.get("output")
            logger.info("[%s] factory completed: +%d %s", firm, count, output)
        case EventType.CONTRACT_SENT:
            to = event.data.get("to")
            side = event.data.get("side")
            qty = event.data.get("quantity")
            comm = event.data.get("commodity")
            price = event.data.get("price_per_unit")
            logger.info("[%s] contract sent to %s: %s %d %s @ $%s/unit", firm, to, side, qty, comm, price)
        case EventType.CONTRACT_ACCEPTED:
            buyer = event.data.get("buyer")
            seller = event.data.get("seller")
            qty = event.data.get("quantity")
            comm = event.data.get("commodity")
            total = event.data.get("total_price")
            logger.info("[%s] contract accepted: %s sold %d %s to %s for $%s", firm, seller, qty, comm, buyer, total)
        case EventType.MESSAGE_SENT:
            to = event.data.get("to")
            content = event.data.get("content", "")[:80]
            logger.info("[%s] message to %s: %s", firm, to, content)
        case EventType.AGENT_ERROR:
            logger.error("[%s] error: %s", firm, event.data.get("error"))
        case _:
            pass


async def run_game() -> None:
    event_bus = EventBus()
    engine = GameEngine(event_bus)
    engine.setup_starting_state()

    event_bus.subscribe_all(log_event)

    agents = [Agent(cfg["id"], engine, event_bus) for cfg in FIRM_CONFIGS]

    logger.info("=== ECONOMY SIMULATOR ===")
    logger.info("Game starting with %d firms for %d seconds", len(agents), GAME_DURATION_SECONDS)

    engine.start_game()

    await event_bus.publish(Event(
        type=EventType.GAME_STARTED,
        data={},
        timestamp=time.time(),
    ))

    agent_tasks = [asyncio.create_task(a.run()) for a in agents]

    await asyncio.sleep(GAME_DURATION_SECONDS)

    logger.info("=== TIME'S UP ===")
    engine.stop_game()

    for a in agents:
        a.stop()

    # Give agents a moment to finish current operations
    await asyncio.gather(*agent_tasks, return_exceptions=True)

    await event_bus.publish(Event(
        type=EventType.GAME_ENDED,
        data={},
        timestamp=time.time(),
    ))

    # Print results
    results = engine.get_results()
    logger.info("=== FINAL RESULTS ===")
    for i, r in enumerate(results):
        logger.info(
            "#%d %s: $%.2f cash | inventory: %s | factories: %s",
            i + 1,
            r["name"],
            r["cash"],
            r["inventory"],
            r["factories"],
        )


def main() -> None:
    asyncio.run(run_game())


if __name__ == "__main__":
    main()
