# FuturesFlow Workspace

Self-contained Devin Desktop workspace for the **FuturesFlow** futures swing trading agent.

## Agent Profile
- **Identity:** Confident futures swing trader — reads charts, knows the levels, lets the trend do the talking. Exits are rule-governed, not vibes-governed.
- **Cycle interval:** dynamic via `poll_interval` (10–3600s, default 300s / 5 min; agent adjusts based on activity)
- **Risk tolerance:** Aggressive, sized off setup quality — not off "feeling hot" or chasing losses
- **Hold period:** Swing (2-5 days) — enforced by a stagnation timeout, not just a stated intention
- **Max positions:** 10
- **Watchlist:** ES, NQ, CL, GC, SI, NG, BZ, HG (index + commodity futures)
- **Credentials:** Name `FuturesFlow`, Email `futuresflow@agent.dev`

## Architecture
Modeled on BlitzTrader's hard-rule exit architecture:
- **Non-Negotiable Exit Rules** (hard stop-loss -3%, profit target +6%, stagnation timeout 8 cycles, trend reversal EMA cross, volume dry-up, key level breach) — checked in a fixed order, numbers-before-narrative, every cycle, before any qualitative reasoning about a position.
- **Position Review Checklist** run first each cycle, ahead of scanning for new entries.
- **Price-source reconciliation** — platform price is authoritative over MCP price when they disagree.
- **Signal-family weighting** — entry signals are checked for redundancy (e.g. RSI/MACD/EMA all measuring the same trend) so "4+ signals" isn't false-confirmed by correlated indicators.
- **Circuit breaker and pattern-learning sample-size floor** are hard rules, not suggestions, in `DIRECTIVES.md` / `INSTRUCTIONS.md`.
- **Futures market hours awareness** — checks `/api/market-intel/status` every cycle. No new trades when futures are closed (Sun 18:00 – Fri 17:00 ET).
- **Short/cover support** — futures support both long and short setups.

See `INSTRUCTIONS.md` for the full cycle protocol.

## Prerequisites
1. AI-Trader platform running at `http://localhost:8000`
2. MCP server `liquid-co-invest` configured (for market data tools only)
3. `curl` and `jq` available in PATH
4. `python3` with `yfinance` for technical analysis fallback

## Quick Start
1. Open this workspace folder in Devin Desktop
2. Type `@skills:start-cycle` to bootstrap the agent
3. FuturesFlow will login, fetch config, run the Position Review Checklist on any open positions, then begin autonomous cycles
4. To stop: tell the agent to stop cycling

## Workspace Structure
```
futuresflow/
├── .devin/
│   ├── rules/
│   │   └── futuresflow-identity.md    # Always-on identity & MCP rules
│   └── skills/
│       └── start-cycle/
│           └── SKILL.md               # Bootstrap skill
├── INSTRUCTIONS.md                     # Full cycle protocol: swing entry logic, hard exit rules, journaling
├── DIRECTIVES.md                       # Global directives (risk, journal, focus, circuit-breaker overrides)
├── PREFLIGHT.md                        # Re-read every cycle — exit rules + position review template
├── journal_FuturesFlow.md              # Trade journal (persistent state; numbers-first entries)
├── API_REFERENCE.md                    # Condensed AI-Trader API reference (futures-focused)
├── scan.py                             # Technical analysis helper for futures symbols (yfinance =F suffix)
└── README.md                           # This file
```

## MCP Tool Usage
- **Allowed:** `mcp0_analyze_market`, `mcp0_get_news`, `mcp0_analyze_markets_batch`, `mcp0_get_positioning_pulse`, `mcp0_show_chart`, `mcp0_get_technical_indicators` (market data only)
- **Forbidden:** All trade execution MCP tools — trades go through the AI-Trader API via `curl`, never through an MCP execution tool

## Exit Rules at a Glance
These fire regardless of how the agent's qualitative read of a position looks. Full detail in `INSTRUCTIONS.md`.

| Rule | Trigger |
|---|---|
| Hard stop-loss | -3% |
| Profit target | +6% |
| Stagnation timeout | 8 consecutive cycles with <1% move |
| Trend reversal | EMA 20 crosses below EMA 50 (longs) / above (shorts) |
| Volume dry-up | Volume ratio < 0.4x for 3+ consecutive cycles |
| Key level breach | Price closes below key support (longs) / above key resistance (shorts) |

## Steering
Edit `DIRECTIVES.md` to override focus symbols, risk settings, or add special instructions — the agent reads it every cycle. Note: directives can adjust entry criteria and sizing, but should not be used to disable the Non-Negotiable Exit Rules above; if a directive conflicts with a hard exit rule, the hard rule still applies.
