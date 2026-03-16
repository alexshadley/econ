import asyncio
import logging
import os
import signal
import sys
import termios
import time
import tty
from pathlib import Path

# Load .env file
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

from rich.console import Console
from rich.text import Text

from app.agent import Agent
from app.config import FIRM_CONFIGS, GAME_DURATION_SECONDS
from app.engine import GameEngine
from app.events import Event, EventBus, EventType
from app.save import list_saves, load_save, save_game
from app.tui import GameDisplay

# Suppress console logging — the TUI handles all display.
logging.basicConfig(level=logging.WARNING)


def _read_key() -> str:
    """Read a single keypress. Returns 'up', 'down', 'enter', or the character."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\r" or ch == "\n":
            return "enter"
        if ch == "\x1b":
            seq = sys.stdin.read(2)
            if seq == "[A":
                return "up"
            if seq == "[B":
                return "down"
            return "escape"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _render_menu(console: Console, options: list[str], selected: int) -> None:
    """Clear screen and render the menu with the selected item highlighted."""
    console.clear()
    console.print()
    console.print("[bold bright_cyan]D A E G U[/]  [dim]Economy Simulator[/]")
    console.print()
    for i, label in enumerate(options):
        if i == selected:
            console.print(f"  [bold bright_white on blue] > {label} [/]")
        else:
            console.print(f"    {label}")
    console.print()
    console.print("[dim]↑/↓ to navigate, Enter to select[/]")


def prompt_startup() -> dict | None:
    """Show startup menu. Returns save data dict to resume, or None for new game."""
    console = Console()
    saves = list_saves()

    if not saves:
        return None

    # Build option labels: "New game" first, then saved games
    options: list[str] = ["New game"]
    for s in saves:
        status = "completed" if s["game_completed"] else "in progress"
        cash_parts = [f"{fid}: {cash}" for fid, cash in s["cash_summary"].items()]
        cash_str = ", ".join(cash_parts)
        options.append(f"{s['saved_at']}  ({status})  {cash_str}")

    selected = 0
    _render_menu(console, options, selected)

    while True:
        key = _read_key()
        if key == "up":
            selected = (selected - 1) % len(options)
            _render_menu(console, options, selected)
        elif key == "down":
            selected = (selected + 1) % len(options)
            _render_menu(console, options, selected)
        elif key == "enter":
            console.clear()
            if selected == 0:
                return None
            save_path = saves[selected - 1]["path"]
            return load_save(save_path)


async def run_game(save_data: dict | None = None) -> None:
    resumed = save_data is not None

    event_bus = EventBus()
    engine = GameEngine(event_bus)

    if save_data:
        engine.restore_from_save(save_data)
    else:
        engine.setup_starting_state()

    display = GameDisplay(engine)
    event_bus.subscribe_all(display.handle_event)

    agents = [Agent(cfg["id"], engine, event_bus) for cfg in FIRM_CONFIGS]

    # Signal handling: use an event to interrupt the main sleep
    shutdown_event = asyncio.Event()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown_event.set)

    engine.start_game()
    display.start()

    await event_bus.publish(Event(
        type=EventType.GAME_STARTED,
        data={},
        timestamp=time.time(),
    ))

    agent_tasks = [asyncio.create_task(a.run(resumed=resumed)) for a in agents]
    refresh_task = asyncio.create_task(display.run_refresh_loop())
    key_task = asyncio.create_task(display.run_key_listener())

    # Wait for either the game duration to elapse or a shutdown signal
    timer_task = asyncio.create_task(asyncio.sleep(GAME_DURATION_SECONDS))
    signal_task = asyncio.create_task(shutdown_event.wait())

    done, pending = await asyncio.wait(
        [timer_task, signal_task, *agent_tasks],
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        t.cancel()

    # If an agent task crashed, re-raise its exception immediately
    for t in done:
        if t in agent_tasks and t.exception() is not None:
            # Stop TUI so the traceback is visible
            display.stop()
            raise t.exception()

    interrupted = shutdown_event.is_set()
    game_completed = not interrupted

    # Cancel all remaining tasks immediately
    refresh_task.cancel()
    key_task.cancel()
    await asyncio.gather(*agent_tasks, refresh_task, key_task, return_exceptions=True)

    engine.stop_game()

    # Stop TUI before saving so terminal is clean
    display.stop()

    # Save game state
    save_path = save_game(engine, game_completed=game_completed)

    if interrupted:
        console = Console()
        console.print()
        console.print(f"[bold yellow]Game interrupted.[/] State saved to [dim]{save_path.name}[/]")
        console.print()
    else:
        await event_bus.publish(Event(
            type=EventType.GAME_ENDED,
            data={},
            timestamp=time.time(),
        ))

        # Re-enter TUI briefly to show results
        results = engine.get_results()
        display.start()
        display.show_results(results)
        await asyncio.sleep(8)
        display.stop()

        display.print_summary(results)


def main() -> None:
    save_data = prompt_startup()
    asyncio.run(run_game(save_data))


if __name__ == "__main__":
    main()
