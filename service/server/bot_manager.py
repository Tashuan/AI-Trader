import os
import sys
import threading
from dataclasses import dataclass
from typing import Optional


@dataclass
class ManagedBot:
    agent_key: str
    thread: threading.Thread
    stop_event: threading.Event
    started_at: float


_bots: dict[str, ManagedBot] = {}
_lock = threading.Lock()


def _ensure_agents_path(agents_dir: str) -> None:
    if agents_dir not in sys.path:
        sys.path.insert(0, agents_dir)


def start_bot(agent_key: str, agents_dir: str, api_base: str = "http://localhost:8000/api") -> dict:
    with _lock:
        existing = _bots.get(agent_key)
        if existing and existing.thread.is_alive():
            return {"success": False, "message": f"Bot '{agent_key}' is already running"}

        try:
            import time
            _ensure_agents_path(agents_dir)
            from run_agents import create_agent, run_agent_thread

            agent = create_agent(agent_key, api_base, 60)
            stop_event = threading.Event()
            thread = threading.Thread(
                target=run_agent_thread,
                args=(agent, 0, stop_event),
                name=f"ManagedBot-{agent_key}",
                daemon=True,
            )
            thread.start()
            _bots[agent_key] = ManagedBot(
                agent_key=agent_key,
                thread=thread,
                stop_event=stop_event,
                started_at=time.time(),
            )
            return {
                "success": True,
                "message": f"Started bot '{agent_key}'",
                "thread": thread.name,
            }
        except Exception as e:
            return {"success": False, "message": f"Failed to start bot: {e}"}


def stop_bot(agent_key: str) -> dict:
    with _lock:
        bot = _bots.get(agent_key)
        if not bot or not bot.thread.is_alive():
            _bots.pop(agent_key, None)
            return {"success": False, "message": f"Bot '{agent_key}' is not running"}

        bot.stop_event.set()
        bot.thread.join(timeout=10)
        running = bot.thread.is_alive()
        if not running:
            _bots.pop(agent_key, None)
        return {
            "success": not running,
            "message": f"{'Stopped' if not running else 'Stop requested for'} bot '{agent_key}'",
        }


def stop_bot_by_name(agent_name: str) -> bool:
    key = agent_name.lower()
    result = stop_bot(key)
    return result["success"]


def get_bot_status(agent_key: str) -> dict:
    with _lock:
        bot = _bots.get(agent_key)
        if not bot or not bot.thread.is_alive():
            _bots.pop(agent_key, None)
            return {"running": False, "pid": None, "thread": None}
        return {"running": True, "pid": None, "thread": bot.thread.name}


def get_all_bot_statuses() -> dict[str, dict]:
    with _lock:
        result = {}
        dead = []
        for key, bot in _bots.items():
            if bot.thread.is_alive():
                result[key] = {"running": True, "pid": None, "thread": bot.thread.name}
            else:
                dead.append(key)
        for key in dead:
            _bots.pop(key, None)
        return result


def disconnect_agent(agent_id: int) -> dict:
    """Force-disconnect an AI agent by rotating their token.

    This invalidates their current session — their next API call
    will return 401 and they'll need to login again.
    """
    try:
        from services import _issue_agent_token
        _issue_agent_token(agent_id)
        return {"success": True, "message": "Agent disconnected (token rotated)"}
    except Exception as e:
        return {"success": False, "message": f"Failed to disconnect: {e}"}
