# Fence Bar Strategy Lab

## Overview

A standalone, configuration-driven research framework for testing the **Fence Bar** opening-range breakout strategy before integrating it into the live trading platform.

The Fence Bar strategy is a simple, rules-based approach designed for the first 15–30 minutes of the US market open. It avoids the chaotic opening minutes by waiting for a clear directional commitment before entering.

## The Strategy

The strategy follows five steps every trading session:

1. **Draw the Fence** — Mark the high and low of the first 5-minute candle (9:30–9:35 AM ET) as the "top rail" and "bottom rail."
2. **Wait for Breakout** — Wait for a subsequent 5-minute candle to close entirely outside the fence rails, indicating directional commitment.
3. **Retest Entry** — Wait for a later candle to wick back into the fence area and close back outside it. Enter at the close of this retest candle.
4. **Defined Risk** — Place the stop loss at the fence midpoint (50% into the range).
5. **Profit Target** — Target a fixed 2R multiple of the entry-to-stop distance.

Additional contextual filters:

- **20-period SMA anchor** — Optionally reject trades where price is too extended from the 20 SMA, or require trend alignment.
- **One trade per day** — No re-entry after a stopped-out trade.
- **Force exit** — Open positions are closed before the end of the session.

## What Was Built

A reusable strategy lab with four layers:

| Layer | Purpose |
|---|---|
| **Strategy config** | JSON configuration file controlling all rules and thresholds |
| **Pure strategy logic** | Stateless state machine that processes one bar at a time |
| **Historical replay engine** | Backtester that fetches 5m data, simulates fills, tracks P&L |
| **CLI integration** | Run backtests via `run_backtest.py fencebar` |

The strategy logic is completely decoupled from the platform — no agent IDs, no auth tokens, no database tables, no live order APIs. This allows rapid iteration and validation before any live integration.

## How to Run

```bash
# List available strategies
python agents/run_backtest.py --list

# Run Fence Bar backtest on QQQ
python agents/run_backtest.py fencebar \
  --symbols QQQ \
  --start 2025-01-01 \
  --end 2025-12-31 \
  --cache \
  --json fence_bar_report.json

# Run on multiple symbols (selects one per session)
python agents/run_backtest.py fencebar \
  --symbols QQQ,SPY,NVDA,AMD,TSLA \
  --start 2025-01-01 \
  --end 2025-12-31 \
  --cache
```

## Configuration

All strategy rules are controlled via `agents/config/fence_bar.json`. Key sections:

- **Session** — Market open, fence window, latest breakout time, force-exit time
- **Fence** — Minimum and maximum acceptable range width
- **Breakout** — Body-outside requirement, minimum close distance, timeout
- **Retest** — Max bars after breakout, wick requirement, close-back-outside requirement
- **Anchor** — 20 SMA filter settings (enabled, period, max distance, extended action)
- **Risk** — Stop mode, target R-multiple, risk per trade, max trades per day
- **Execution** — Slippage and fee assumptions

## Reusability for Future Strategies

The config-loading utilities and the separation between pure logic and replay engine are designed to be reusable. Future standalone strategies (Opening Power Bar, VWAP Reversion, ORB, etc.) can follow the same pattern:

1. Define a JSON config
2. Implement a pure strategy module
3. Implement or reuse a backtest adapter
4. Add a CLI entry point

## Current Status

- Strategy logic implemented and unit-tested
- Historical replay engine working with synthetic and real data
- CLI integration complete
- Not yet validated against real historical data — that is the next step

## Next Steps

1. Run the backtest across a large historical sample (6–12 months)
2. Compare parameter variants (1R vs 2R, strict vs relaxed, SMA on/off)
3. Test long-only, short-only, and both directions separately
4. Validate across different symbols and market regimes
5. If expectancy is positive after costs, build the live platform runner
