import os
import sys
import time
import traceback
import threading
from dataclasses import dataclass, field
from typing import Optional, Callable, Any


@dataclass
class ManagedBot:
    agent_key: str
    thread: threading.Thread
    stop_event: threading.Event
    started_at: float
    bot_type: str = "strategy"  # "strategy" (BaseAgent) or "runner" (deterministic Goal Runner)
    last_error: str | None = None
    last_error_at: float | None = None


_bots: dict[str, ManagedBot] = {}
_lock = threading.Lock()

# Keep last error for dead bots so the UI can show why they stopped
_dead_bot_errors: dict[str, tuple[str, float]] = {}
_DEAD_ERROR_TTL = 300  # 5 minutes


def _ensure_agents_path(agents_dir: str) -> None:
    if agents_dir not in sys.path:
        sys.path.insert(0, agents_dir)


def _wrap_target(key: str, target: Callable, args: tuple) -> Callable[[], None]:
    """Wrap a thread target so crashes are captured and stored for the UI."""
    def wrapper():
        try:
            target(*args)
        except SystemExit:
            pass
        except Exception:
            err = traceback.format_exc()
            with _lock:
                bot = _bots.get(key)
                if bot:
                    bot.last_error = err
                    bot.last_error_at = time.time()
                _dead_bot_errors[key] = (err, time.time())
        else:
            # Thread exited cleanly (stop_event was set)
            with _lock:
                _dead_bot_errors.pop(key, None)
    return wrapper


def start_bot(agent_key: str, agents_dir: str, api_base: str = "http://localhost:8000/api") -> dict:
    with _lock:
        existing = _bots.get(agent_key)
        if existing and existing.thread.is_alive():
            return {"success": False, "message": f"Bot '{agent_key}' is already running"}

        try:
            _ensure_agents_path(agents_dir)
            from run_agents import create_agent, run_agent_thread

            agent = create_agent(agent_key, api_base, 60)
            stop_event = threading.Event()
            thread = threading.Thread(
                target=_wrap_target(agent_key, run_agent_thread, (agent, 0, stop_event)),
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
            err = _get_dead_error(agent_key)
            return {"running": False, "pid": None, "thread": None, "last_error": err}
        return {"running": True, "pid": None, "thread": bot.thread.name, "last_error": None}


def _get_dead_error(key: str) -> str | None:
    """Get error for a dead bot, with TTL cleanup."""
    entry = _dead_bot_errors.get(key)
    if not entry:
        return None
    err, ts = entry
    if time.time() - ts > _DEAD_ERROR_TTL:
        _dead_bot_errors.pop(key, None)
        return None
    return err


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
                    "last_error": None,
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
            _ensure_agents_path(agents_dir)
            from blitz_runner import run_loop

            stop_event = threading.Event()
            thread = threading.Thread(
                target=_wrap_target(_RUNNER_KEY, run_loop, (stop_event, poll_interval)),
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
            return {"running": False, "pid": None, "thread": None, "bot_type": "runner", "last_error": _get_dead_error(_RUNNER_KEY)}
        return {"running": True, "pid": None, "thread": bot.thread.name, "bot_type": "runner", "last_error": None}


# ── ScalpRunner management ─────────────────────────────────────────────

_SCALP_RUNNER_KEY = "scalprunner-runner"


def start_scalp_runner(agents_dir: str, poll_interval: int = 15) -> dict:
    """Start the deterministic ScalpRunner 4-step agent in a thread."""
    with _lock:
        existing = _bots.get(_SCALP_RUNNER_KEY)
        if existing and existing.thread.is_alive():
            return {"success": False, "message": "ScalpRunner is already running"}

        try:
            _ensure_agents_path(agents_dir)
            from scalp_runner import run_loop

            stop_event = threading.Event()
            thread = threading.Thread(
                target=_wrap_target(_SCALP_RUNNER_KEY, run_loop, (stop_event, poll_interval)),
                name="ManagedRunner-scalprunner",
                daemon=True,
            )
            thread.start()
            _bots[_SCALP_RUNNER_KEY] = ManagedBot(
                agent_key=_SCALP_RUNNER_KEY,
                thread=thread,
                stop_event=stop_event,
                started_at=time.time(),
                bot_type="runner",
            )
            return {
                "success": True,
                "message": "Started ScalpRunner (deterministic 4-step scalp agent)",
                "thread": thread.name,
            }
        except Exception as e:
            return {"success": False, "message": f"Failed to start ScalpRunner: {e}"}


def stop_scalp_runner() -> dict:
    """Stop the deterministic ScalpRunner agent."""
    return stop_bot(_SCALP_RUNNER_KEY)


def get_scalp_runner_status() -> dict:
    """Get the status of the ScalpRunner agent."""
    with _lock:
        bot = _bots.get(_SCALP_RUNNER_KEY)
        if not bot or not bot.thread.is_alive():
            _bots.pop(_SCALP_RUNNER_KEY, None)
            return {"running": False, "pid": None, "thread": None, "bot_type": "runner", "last_error": _get_dead_error(_SCALP_RUNNER_KEY)}
        return {"running": True, "pid": None, "thread": bot.thread.name, "bot_type": "runner", "last_error": None}


# ── CryptoRunner management ────────────────────────────────────────────

_CRYPTO_RUNNER_KEY = "cryptorunner-runner"


def start_crypto_runner(agents_dir: str, poll_interval: int = 1800) -> dict:
    """Start the deterministic CryptoRunner bot in a thread."""
    with _lock:
        existing = _bots.get(_CRYPTO_RUNNER_KEY)
        if existing and existing.thread.is_alive():
            return {"success": False, "message": "CryptoRunner is already running"}

        try:
            _ensure_agents_path(agents_dir)
            from crypto_runner import run_loop

            stop_event = threading.Event()
            thread = threading.Thread(
                target=_wrap_target(_CRYPTO_RUNNER_KEY, run_loop, (stop_event, poll_interval)),
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
            return {"running": False, "pid": None, "thread": None, "bot_type": "runner", "last_error": _get_dead_error(_CRYPTO_RUNNER_KEY)}
        return {"running": True, "pid": None, "thread": bot.thread.name, "bot_type": "runner", "last_error": None}


# ── StockBoy supervisor management ─────────────────────────────────────

def start_stockboy() -> dict:
    """Start the StockBoy platform supervisor loop."""
    try:
        from stockboy_manager import start
        return start()
    except Exception as exc:
        return {"success": False, "message": f"Failed to start StockBoy: {exc}"}


def stop_stockboy() -> dict:
    """Stop StockBoy without stopping runners or modifying positions."""
    try:
        from stockboy_manager import stop
        return stop()
    except Exception as exc:
        return {"success": False, "message": f"Failed to stop StockBoy: {exc}"}


def get_stockboy_status() -> dict:
    """Return StockBoy supervisor status."""
    try:
        from stockboy_manager import status
        return status()
    except Exception as exc:
        return {"running": False, "bot_type": "supervisor", "last_error": str(exc)}
