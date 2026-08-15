"""
test_runner_narrative.py — Tests for the stdout-only structured narrative layer.

Verifies:
1. RunnerNarrative emits structured JSON events to the printer.
2. Throttling suppresses repeat events within the configured window.
3. Per-cycle detail caps drop low-priority detail events past the limit.
4. Phrase rendering pulls from the runner's profile when available.
5. begin_cycle / recap / flush behave as documented.
"""

import json
import sys
import os
import time

sys.path.insert(0, os.path.dirname(__file__))

from runner_narrative import NarrativeConfig, RunnerNarrative, NarrativeEvent


# ─── Helpers ────────────────────────────────────────────────────

class FakePrinter:
    """Captures every line emitted by a RunnerNarrative."""
    def __init__(self):
        self.lines: list[str] = []

    def __call__(self, text: str) -> None:
        self.lines.append(text)

    def json_events(self) -> list[dict]:
        events = []
        for line in self.lines:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("event_version") == 1:
                events.append(payload)
        return events


def _make_narrative(runner: str = "scalprunner", profile: dict | None = None) -> tuple[RunnerNarrative, FakePrinter]:
    config = NarrativeConfig()
    if profile is not None:
        config.data = {"default": {}, "runners": {runner: profile}}
    printer = FakePrinter()
    narrative = RunnerNarrative(runner, config=config, printer=printer)
    return narrative, printer


# ─── Tests ──────────────────────────────────────────────────────

def test_emits_structured_json_event():
    narrative, printer = _make_narrative(profile={"throttle_seconds": 0})
    event = narrative.emit("scan", "scan", "started", priority="action", facts={"watchlist_size": 3})
    assert event is not None, "emit should return a NarrativeEvent"
    assert isinstance(event, NarrativeEvent)
    events = printer.json_events()
    assert len(events) == 1
    payload = events[0]
    assert payload["runner"] == "scalprunner"
    assert payload["phase"] == "scan"
    assert payload["kind"] == "scan"
    assert payload["priority"] == "action"
    assert payload["facts"]["watchlist_size"] == 3
    assert payload["event_id"] == event.event_id
    print("OK: structured JSON event emitted")


def test_throttle_suppresses_repeats():
    profile = {"throttle_seconds": 0.05}
    narrative, printer = _make_narrative(profile=profile)
    first = narrative.emit("scan", "scan", "started", priority="info", facts={"i": 1})
    second = narrative.emit("scan", "scan", "started", priority="info", facts={"i": 2})
    assert first is not None
    assert second is None, "second emit inside throttle window should be suppressed"
    assert printer.json_events() == [first.as_dict()]
    print("OK: throttle suppresses repeats within window")


def test_throttle_releases_after_window():
    profile = {"throttle_seconds": 0.02}
    narrative, printer = _make_narrative(profile=profile)
    narrative.emit("scan", "scan", "started", priority="info")
    time.sleep(0.03)
    second = narrative.emit("scan", "scan", "started", priority="info")
    assert second is not None, "emit after the throttle window should succeed"
    assert len(printer.json_events()) == 2
    print("OK: throttle releases after window expires")


def test_detail_cap_drops_low_priority_details():
    profile = {"max_detail_events_per_cycle": 2}
    narrative, printer = _make_narrative(profile=profile)
    narrative.begin_cycle(1)
    for i in range(5):
        narrative.emit("scan", "candidate", "observed", priority="info", detail=True, facts={"i": i})
    events = printer.json_events()
    assert len(events) == 2, f"only the first 2 detail events should emit, got {len(events)}"
    print("OK: detail cap drops low-priority detail events")


def test_detail_cap_keeps_critical_after_limit():
    profile = {"max_detail_events_per_cycle": 1}
    narrative, printer = _make_narrative(profile=profile)
    narrative.begin_cycle(1)
    narrative.emit("scan", "candidate", "observed", priority="info", detail=True)
    critical = narrative.emit("scan", "candidate", "alert", priority="critical", detail=True)
    assert critical is not None, "critical priority should bypass the detail cap"
    print("OK: detail cap keeps critical-priority events")


def test_phrase_rendering_uses_profile():
    profile = {"phrases": {"scan": ["Scanning {watchlist_size} symbols like a hawk."]}}
    narrative, printer = _make_narrative(profile=profile)
    event = narrative.emit("scan", "scan", "started", priority="action", facts={"watchlist_size": 7})
    assert event is not None
    assert event.message == "Scanning 7 symbols like a hawk."
    # First printer line is the rendered human message
    assert printer.lines[0] == "[Scalprunner] Scanning 7 symbols like a hawk."
    print("OK: phrase rendering pulls from profile")


def test_phrase_rendering_falls_back_on_missing_kind():
    narrative, printer = _make_narrative(profile={"throttle_seconds": 0})
    event = narrative.emit("scan", "unknown_kind", "observed", priority="action")
    assert event is not None
    # Fallback format is "{phase}: {outcome}"
    assert event.message == "scan: observed"
    print("OK: phrase rendering falls back when kind is missing")


def test_begin_cycle_resets_per_cycle_state():
    narrative, printer = _make_narrative(profile={"throttle_seconds": 0})
    narrative.begin_cycle(1)
    narrative.emit("scan", "phase", priority="info")
    narrative.emit("scan", "candidate", priority="info", detail=True)
    first_cycle_id = narrative.cycle_id
    narrative.begin_cycle(2)
    assert narrative.cycle_id != first_cycle_id
    assert narrative._detail_count == 0
    assert narrative._event_count == 0
    print("OK: begin_cycle resets per-cycle state")


def test_recap_emits_summary_event():
    narrative, printer = _make_narrative(profile={"throttle_seconds": 0})
    narrative.begin_cycle(1)
    narrative.emit("scan", "scan", "started", priority="action")
    narrative.emit("scan", "scan", "complete", priority="action")
    narrative.recap({"duration_seconds": 1.5})
    events = printer.json_events()
    recap = events[-1]
    assert recap["phase"] == "summary"
    assert recap["kind"] == "cycle_recap"
    assert recap["facts"]["emitted_events"] == 2
    assert recap["facts"]["duration_seconds"] == 1.5
    print("OK: recap emits summary event with counts")


def test_flush_reports_suppressed_events():
    profile = {"throttle_seconds": 60}
    narrative, printer = _make_narrative(profile=profile)
    narrative.emit("scan", "scan", "started", priority="info")
    narrative.emit("scan", "scan", "started", priority="info")  # suppressed
    narrative.emit("scan", "scan", "started", priority="info")  # suppressed
    narrative.flush()
    events = printer.json_events()
    summary = events[-1]
    assert summary["phase"] == "summary"
    assert summary["kind"] == "aggregate"
    assert summary["facts"]["suppressed_total"] == 2
    print("OK: flush reports suppressed event counts")


def test_symbol_propagates_into_facts():
    narrative, printer = _make_narrative(profile={"throttle_seconds": 0})
    event = narrative.emit("entry", "entry", "complete", priority="trade", symbol="AAPL", facts={"side": "long"})
    assert event is not None
    assert event.symbol == "AAPL"
    assert event.facts["symbol"] == "AAPL"
    print("OK: symbol propagates into facts and event field")


# ─── Runner ─────────────────────────────────────────────────────

def main():
    tests = [
        test_emits_structured_json_event,
        test_throttle_suppresses_repeats,
        test_throttle_releases_after_window,
        test_detail_cap_drops_low_priority_details,
        test_detail_cap_keeps_critical_after_limit,
        test_phrase_rendering_uses_profile,
        test_phrase_rendering_falls_back_on_missing_kind,
        test_begin_cycle_resets_per_cycle_state,
        test_recap_emits_summary_event,
        test_flush_reports_suppressed_events,
        test_symbol_propagates_into_facts,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL: {test.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"ERROR: {test.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
