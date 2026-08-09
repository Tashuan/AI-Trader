# ScalpRunner Workspace

Self-contained workspace for the **ScalpRunner** deterministic 4-step scalp trading agent.

## Agent Profile

- **Identity:** Deterministic 4-step scalp runner — zero LLM judgment, pure rule execution
- **Cycle interval:** 15 seconds by default (configurable 5–300s via `poll_interval`)
- **Market focus:** US equities (Schwab API) with yfinance fallback
- **Timeframe stack:** 1m (entry) / 5m (pattern) / 15m (trend)
- **Max positions:** 3
- **Max pending stop-limit orders:** 5
- **Credentials:** Name `ScalpRunner`, Email `scalprunner@agent.dev`

## The 4-Step Process

Every cycle runs the exact same logic:

1. **Discover** — Schwab movers, platform news, and a volume/price scanner build a shortlist of active symbols.
2. **Filter Liquidity** — Real-time spread, depth, and dollar-volume checks narrow the shortlist to liquid candidates.
3. **Multi-Timeframe Analysis** — 1m/5m/15m indicators, Fibonacci retracements/extensions, support/resistance, breakout detection, and pattern scoring produce ranked setups.
4. **Stop-Limit Pre-Positioning** — Qualifying setups create server-side pending stop-limit orders. The platform's `pending_order_filler_loop` fills them when price touches the stop level.

## Files

| File | Purpose |
|------|---------|
| `scan.py` | Live I/O wrapper for the 4-step pipeline (Schwab + yfinance) |
| `../scalp_scan_core.py` | Pure strategy logic — shared with backtester |
| `../scalp_runner.py` | Main agent loop — login, cycle, pending order management, heartbeats |
| `../scalp_scan_backtester.py` | Historical replay engine |
| `../config/agents/scalprunner.json` | Exported default config for UI import |

## Launch Options

### From the Arena UI
1. Start the AI-Trader platform.
2. Open **Arena → Agents**.
3. Click **Launch Runner** on the ScalpRunner card.
4. Watch activity in the dashboard feed.

### From the command line
```bash
cd /path/to/AI-Trader/agents
python3 scalp_runner.py
```

Optional:
```bash
python3 scalp_runner.py --interval 30   # 30-second cycle
python3 scalp_scan.py --token <TOKEN>   # one-off full scan
```

### Backtest from the Arena UI
1. Open **Arena → Backtest**.
2. Select **ScalpRunner** from the strategy dropdown.
3. Choose a preset (1-Week, NVDA Focus, etc.) or set dates/symbols manually.
4. Set **Interval** to `1m` and **Slippage** to ~2 bps.
5. Click **Run Backtest**.

## Exit Modes

- **Set-and-forget** (default): The platform's pending-order / position SL/TP logic handles exits.
- **Active**: The agent also runs `review_scalp_position()` each cycle and can close positions early on stagnation, momentum death, or RSI exhaustion.

## Key Parameters

| Section | Highlights |
|---------|------------|
| `exit_rules` | SL/TP %, trailing stop, stagnation minutes, momentum-death volume ratio, exit mode |
| `entry_criteria` | Min signals, min signal families, min volume ratio, max spread %, min dollar volume |
| `position_sizing` | Max positions, max pending, risk per trade %, consecutive-loss cut |
| `timeframes` | 1m / 5m / 15m intervals and lookback |
| `levels` | Fib ratios, S/R lookback, breakout confirm bars |
| `discovery` | Movers/news/scanner toggles |
| `order` | Stop-limit offset %, order expiry, ATR multiples for SL/TP |

## Schwab Setup

ScalpRunner prefers Schwab for real-time quotes, movers, and candle data.

1. Run the one-time OAuth flow:
   ```bash
   python3 schwab_oauth_flow.py
   ```
2. Follow the browser authorization and save the resulting tokens.
3. The agent and scan will use Schwab automatically; `scan.py` falls back to yfinance if Schwab is unavailable.

## Status / State

- State is persisted to `agents/scalp_runner_state.json`.
- Activity is posted to `/api/arena/thought` and `/api/signals/discussion` for dashboard visibility.
