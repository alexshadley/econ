import asyncio
import logging
import os
import time
from pathlib import Path

# Load .env file
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

from app.agent import Agent
from app.config import FIRM_CONFIGS, GAME_DURATION_SECONDS
from app.engine import GameEngine
from app.events import Event, EventBus, EventType
from app.tui import GameDisplay

# Suppress console logging — the TUI handles all display.
logging.basicConfig(level=logging.WARNING)


async def run_game() -> None:
    event_bus = EventBus()
    engine = GameEngine(event_bus)
    engine.setup_starting_state()

    display = GameDisplay(engine)
    event_bus.subscribe_all(display.handle_event)

    agents = [Agent(cfg["id"], engine, event_bus) for cfg in FIRM_CONFIGS]

    engine.start_game()
    display.start()

    await event_bus.publish(Event(
        type=EventType.GAME_STARTED,
        data={},
        timestamp=time.time(),
    ))

    agent_tasks = [asyncio.create_task(a.run()) for a in agents]
    refresh_task = asyncio.create_task(display.run_refresh_loop())

    await asyncio.sleep(GAME_DURATION_SECONDS)

    engine.stop_game()

    for a in agents:
        a.stop()

    await asyncio.gather(*agent_tasks, return_exceptions=True)
    refresh_task.cancel()

    await event_bus.publish(Event(
        type=EventType.GAME_ENDED,
        data={},
        timestamp=time.time(),
    ))

    # Show results on the TUI for a few seconds, then exit to normal terminal.
    results = engine.get_results()
    display.show_results(results)
    await asyncio.sleep(8)
    display.stop()

    display.print_summary(results)


def main() -> None:
    asyncio.run(run_game())


if __name__ == "__main__":
    main()
