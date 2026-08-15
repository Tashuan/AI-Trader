"""Stdout-only structured narration for deterministic runners."""

from __future__ import annotations

import json
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


_CONFIG_PATH = Path(__file__).with_name("config") / "runner_narratives.json"


@dataclass
class NarrativeEvent:
    runner: str
    cycle_id: str
    phase: str
    kind: str
    priority: str
    outcome: str
    message: str
    facts: dict[str, Any] = field(default_factory=dict)
    symbol: str = ""
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    event_version: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_version": self.event_version,
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "runner": self.runner,
            "cycle_id": self.cycle_id,
            "phase": self.phase,
            "kind": self.kind,
            "priority": self.priority,
            "outcome": self.outcome,
            "symbol": self.symbol,
            "message": self.message,
            "facts": self.facts,
        }


class NarrativeConfig:
    def __init__(self, path: Path = _CONFIG_PATH):
        self.data: dict[str, Any] = {}
        try:
            self.data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            self.data = {}

    def profile(self, runner: str) -> dict[str, Any]:
        profiles = self.data.get("runners", {})
        return profiles.get(runner, self.data.get("default", {}))


class RunnerNarrative:
    """Render and emit bounded events without persistence or network I/O."""

    _always = {"critical", "action", "error"}

    def __init__(
        self,
        runner: str,
        config: NarrativeConfig | None = None,
        printer: Callable[[str], None] = print,
    ):
        self.runner = runner.lower()
        self.config = config or NarrativeConfig()
        self.profile = self.config.profile(self.runner)
        self.printer = printer
        self.cycle_id = "startup"
        self.cycle_count = 0
        self._phase_seen: set[str] = set()
        self._detail_count = 0
        self._event_count = 0
        self._suppressed: dict[str, int] = {}
        self._last_emit: dict[str, float] = {}

    def begin_cycle(self, cycle: int | str) -> str:
        self.flush()
        self.cycle_count = int(cycle) if str(cycle).isdigit() else self.cycle_count + 1
        self.cycle_id = f"{self.runner}-{self.cycle_count}-{uuid.uuid4().hex[:8]}"
        self._phase_seen.clear()
        self._detail_count = 0
        self._event_count = 0
        self._suppressed.clear()
        return self.cycle_id

    def _phrase(self, kind: str, fallback: str, facts: dict[str, Any]) -> str:
        phrases = self.profile.get("phrases", {}).get(kind, [])
        if not phrases:
            return fallback
        seed = f"{self.cycle_id}:{kind}:{facts.get('symbol', '')}"
        choice = random.Random(seed).choice(phrases)
        try:
            return choice.format(**facts)
        except (KeyError, ValueError):
            return choice

    def emit(
        self,
        phase: str,
        kind: str,
        outcome: str = "observed",
        *,
        facts: dict[str, Any] | None = None,
        message: str = "",
        priority: str = "info",
        symbol: str = "",
        detail: bool = False,
        throttle_key: str = "",
    ) -> NarrativeEvent | None:
        facts = dict(facts or {})
        if symbol:
            facts.setdefault("symbol", symbol)
        key = throttle_key or f"{phase}:{kind}:{symbol}"
        now = time.monotonic()
        throttle_seconds = float(self.profile.get("throttle_seconds", 0))
        if throttle_seconds and now - self._last_emit.get(key, 0) < throttle_seconds:
            self._suppressed[key] = self._suppressed.get(key, 0) + 1
            return None
        if priority not in self._always and phase in self._phase_seen and kind == "phase":
            self._suppressed[key] = self._suppressed.get(key, 0) + 1
            return None
        max_details = int(self.profile.get("max_detail_events_per_cycle", 8))
        if detail and priority not in self._always and self._detail_count >= max_details:
            self._suppressed[key] = self._suppressed.get(key, 0) + 1
            return None

        self._last_emit[key] = now
        self._phase_seen.add(phase if kind == "phase" else "")
        self._detail_count += int(detail)
        self._event_count += 1
        facts.setdefault("cycle_events_so_far", self._event_count)
        rendered = message or self._phrase(kind, f"{phase}: {outcome}", facts)
        event = NarrativeEvent(
            runner=self.runner,
            cycle_id=self.cycle_id,
            phase=phase,
            kind=kind,
            priority=priority,
            outcome=outcome,
            symbol=symbol,
            message=rendered,
            facts=facts,
        )
        try:
            self.printer(f"[{self.runner.title()}] {rendered}")
            self.printer(json.dumps(event.as_dict(), sort_keys=True, default=str))
        except Exception:
            return None
        return event

    def flush(self) -> None:
        if not self._suppressed:
            return
        counts = sorted(self._suppressed.items(), key=lambda item: item[1], reverse=True)
        total = sum(self._suppressed.values())
        examples = [key for key, _ in counts[:3]]
        self._suppressed.clear()
        self.emit(
            "summary",
            "aggregate",
            "suppressed",
            priority="info",
            facts={"suppressed_total": total, "representative_keys": examples},
            message=self._phrase(
                "aggregate",
                f"I compressed {total} repetitive details into the evidence locker.",
                {"suppressed_total": total},
            ),
            throttle_key="summary:aggregate",
        )

    def recap(self, facts: dict[str, Any] | None = None) -> None:
        self.emit(
            "summary",
            "cycle_recap",
            facts={"emitted_events": self._event_count, **(facts or {})},
            priority="action",
        )


__all__ = ["NarrativeConfig", "NarrativeEvent", "RunnerNarrative"]
