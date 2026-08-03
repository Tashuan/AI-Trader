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
    bot_type: str = "strategy"  # "strategy" (BaseAgent) or "runner" (deterministic Goal Runner)


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
                result[key] = {
                    "running": True,
                    "pid": None,
                    "thread": bot.thread.name,
                    "bot_type": bot.bot_type,
                }
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


# ── Deterministic Goal Runner management ───────────────────────────────

_RUNNER_KEY = "blitztrader-runner"


def start_runner(agents_dir: str, poll_interval: int = 120) -> dict:
    """Start the deterministic BlitzRunner Goal Runner agent in a thread."""
    with _lock:
        existing = _bots.get(_RUNNER_KEY)
        if existing and existing.thread.is_alive():
            return {"success": False, "message": "BlitzRunner is already running"}

        try:
            import time
            _ensure_agents_path(agents_dir)
            from blitz_runner import run_loop

            stop_event = threading.Event()
            thread = threading.Thread(
                target=run_loop,
                args=(stop_event, poll_interval),
                name="ManagedRunner-blitztrader",
                daemon=True,
            )
            thread.start()
            _bots[_RUNNER_KEY] = ManagedBot(
                agent_key=_RUNNER_KEY,
                thread=thread,
                stop_event=stop_event,
                started_at=time.time(),
                bot_type="runner",
            )
            return {
                "success": True,
                "message": "Started BlitzRunner (deterministic Goal Runner)",
                "thread": thread.name,
            }
        except Exception as e:
            return {"success": False, "message": f"Failed to start runner: {e}"}


def stop_runner() -> dict:
    """Stop the deterministic BlitzRunner agent."""
    return stop_bot(_RUNNER_KEY)


def get_runner_status() -> dict:
    """Get the status of the BlitzRunner agent."""
    with _lock:
        bot = _bots.get(_RUNNER_KEY)
        if not bot or not bot.thread.is_alive():
            _bots.pop(_RUNNER_KEY, None)
            return {"running": False, "pid": None, "thread": None, "bot_type": "runner"}
        return {"running": True, "pid": None, "thread": bot.thread.name, "bot_type": "runner"}


# ── CryptoRunner management ────────────────────────────────────────────

_CRYPTO_RUNNER_KEY = "cryptorunner-runner"


def start_crypto_runner(agents_dir: str, poll_interval: int = 1800) -> dict:
    """Start the deterministic CryptoRunner bot in a thread."""
    with _lock:
        existing = _bots.get(_CRYPTO_RUNNER_KEY)
        if existing and existing.thread.is_alive():
            return {"success": False, "message": "CryptoRunner is already running"}

        try:
            import time
            _ensure_agents_path(agents_dir)
            from crypto_runner import run_loop

            stop_event = threading.Event()
            thread = threading.Thread(
                target=run_loop,
                args=(stop_event, poll_interval),
                name="ManagedRunner-cryptorunner",
                daemon=True,
            )
            thread.start()
            _bots[_CRYPTO_RUNNER_KEY] = ManagedBot(
                agent_key=_CRYPTO_RUNNER_KEY,
                thread=thread,
                stop_event=stop_event,
                started_at=time.time(),
                bot_type="runner",
            )
            return {
                "success": True,
                "message": "Started CryptoRunner (deterministic crypto swing bot)",
                "thread": thread.name,
            }
        except Exception as e:
            return {"success": False, "message": f"Failed to start crypto runner: {e}"}


def stop_crypto_runner() -> dict:
    """Stop the deterministic CryptoRunner bot."""
    return stop_bot(_CRYPTO_RUNNER_KEY)


def get_crypto_runner_status() -> dict:
    """Get the status of the CryptoRunner bot."""
    with _lock:
        bot = _bots.get(_CRYPTO_RUNNER_KEY)
        if not bot or not bot.thread.is_alive():
            _bots.pop(_CRYPTO_RUNNER_KEY, None)
            return {"running": False, "pid": None, "thread": None, "bot_type": "runner"}
        return {"running": True, "pid": None, "thread": bot.thread.name, "bot_type": "runner"}
