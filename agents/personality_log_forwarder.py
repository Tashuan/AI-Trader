"""Optional HTTP forwarder for personality-log events to the Arena server.

Runners that want their structured events visible in the Timeline UI can
wrap their RunnerNarrative printer with this forwarder. It is fire-and-forget
with a small in-memory queue and a background thread — no blocking, no
persistence, no retry beyond the single attempt.

Usage::

    from personality_log_forwarder import PersonalityLogForwarder
    from runner_narrative import RunnerNarrative

    fwd = PersonalityLogForwarder(
        endpoint="http://localhost:8000/api/arena/personality-log",
        runner="scalprunner",
    )
    narrative = RunnerNarrative("scalprunner", printer=fwd.printer)
    # ... at shutdown:
    fwd.close()
"""

from __future__ import annotations

import json
import threading
import urllib.request
import urllib.error
from typing import Any


class PersonalityLogForwarder:
    """Buffer events and POST them to the Arena server in a background thread."""

    def __init__(
        self,
        endpoint: str = "http://localhost:8000/api/arena/personality-log",
        runner: str = "",
        batch_endpoint: str | None = None,
        batch_interval: float = 2.0,
    ) -> None:
        self._endpoint = endpoint
        self._batch_endpoint = batch_endpoint or endpoint.replace(
            "/personality-log", "/personality-log/batch"
        )
        self._runner = runner
        self._batch_interval = batch_interval
        self._queue: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if self._batch_endpoint:
            self._thread = threading.Thread(target=self._flush_loop, daemon=True)
            self._thread.start()

    def printer(self, line: str) -> None:
        """Drop-in replacement for ``print`` that detects JSON event lines."""
        stripped = line.strip()
        if not stripped.startswith("{"):
            # Human-readable line — ignore
            return
        try:
            event = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            return
        if not event.get("event_id"):
            return
        with self._lock:
            self._queue.append(event)

    def _flush_loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(self._batch_interval)
            self._flush()

    def _flush(self) -> None:
        with self._lock:
            if not self._queue:
                return
            batch = self._queue[:]
            self._queue.clear()
        try:
            payload = json.dumps({"events": batch}).encode("utf-8")
            req = urllib.request.Request(
                self._batch_endpoint,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
        except (urllib.error.URLError, OSError, Exception):
            # Fire-and-forget — if the server is down, events are lost.
            # The stdout JSON line is still the canonical record.
            pass

    def close(self) -> None:
        self._stop.set()
        self._flush()
        if self._thread:
            self._thread.join(timeout=5)


__all__ = ["PersonalityLogForwarder"]
