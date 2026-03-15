"""Save and load game state to/from disk."""

import json
from datetime import datetime
from pathlib import Path

SAVES_DIR = Path(__file__).resolve().parent.parent / ".saves"


def save_game(engine, game_completed: bool) -> Path:
    """Serialize current game state to a JSON file in .saves/."""
    SAVES_DIR.mkdir(exist_ok=True)

    # Finalize any in-progress factory jobs before saving
    engine.finalize_factory_jobs()

    save_data = {
        "version": 1,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "game_completed": game_completed,
        "firms": engine.to_save_dict(),
        "tool_call_trace": engine.get_tool_call_trace_for_save(),
        "reasoning_trace": engine.get_reasoning_trace_for_save(),
    }

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = SAVES_DIR / f"save_{timestamp}.json"
    path.write_text(json.dumps(save_data, indent=2))
    return path


def list_saves() -> list[dict]:
    """Return metadata for each save file, newest first."""
    if not SAVES_DIR.exists():
        return []

    saves = []
    for path in sorted(SAVES_DIR.glob("save_*.json"), reverse=True):
        try:
            data = json.loads(path.read_text())
            firms = data.get("firms", {})
            cash_summary = {
                fid: f"${fdata['cash']:.2f}"
                for fid, fdata in firms.items()
            }
            saves.append({
                "path": path,
                "saved_at": data.get("saved_at", "unknown"),
                "game_completed": data.get("game_completed", False),
                "cash_summary": cash_summary,
            })
        except (json.JSONDecodeError, KeyError):
            continue

    return saves


def load_save(path: Path) -> dict:
    """Parse and return save data from a JSON file."""
    return json.loads(path.read_text())
