# Strategy Configuration & JSON Backup

> How strategy parameters flow from defaults → database → live execution, and how they are backed up.

## Overview

Every agent's strategy is **fully configurable without code changes**. Parameters are defined in code as defaults, overridable via the database through the admin UI, and automatically backed up to JSON files on every change. The same parameter set drives both live execution and backtesting — there is zero drift.

This document covers:
1. The 3-layer parameter resolution model
2. The schema system that drives the admin UI
3. The JSON backup / restore mechanism
4. The full ScalpRunner parameter reference

## Architecture

```
        ┌──────────────────┐
        │  Code Defaults    │  SCALP_DEFAULT_PARAMS in scalp_scan_core.py
        │  (Layer 1)        │  CRYPTO_DEFAULT_PARAMS in crypto_scan_core.py
        └────────┬──────────┘
                 │ deep_merge
        ┌────────▼──────────┐
        │  Database Config   │  agent_configs table → config_json.strategy_params
        │  (Layer 2)        │  Updated via admin UI (PUT/PATCH endpoints)
        └────────┬──────────┘
                 │ deep_merge
        ┌────────▼──────────┐
        │  Test Override     │  In-memory only, not persisted
        │  (Layer 3)        │  Used by backtest experiments
        └────────┬──────────┘
                 │
        ┌────────▼──────────┐
        │  Effective Params  │  effective_params() → validated dict
        │  (Final)          │  Used by live runner + backtester
        └────────┬──────────┘
                 │
        ┌────────▼──────────┐
        │  JSON Backup       │  config/agents/<agent_name>.json
        │  (Side effect)     │  Atomic write on every config change
        └───────────────────┘
```

## Key Files

| File | Role |
|---|---|
| `agents/scalp_scan_core.py` | `SCALP_DEFAULT_PARAMS` — canonical defaults for ScalpRunner |
| `agents/crypto_scan_core.py` | `CRYPTO_DEFAULT_PARAMS` — canonical defaults for CryptoRunner |
| `agents/scan_core.py` | `DEFAULT_PARAMS` — canonical defaults for BlitzRunner |
| `agents/strategy_registry.py` | Schema definitions, `effective_params()`, validation, UI schema |
| `agents/scalp_runner.py` | Live ScalpRunner — reads params at runtime |
| `agents/workspaces/scalprunner/scan.py` | Live scan pipeline — reads params at runtime |
| `service/server/config_backup.py` | JSON backup/restore/reconcile logic |
| `service/server/routes_agent_manager.py` | Admin API endpoints for config management |

---

## 1. Parameter Resolution

### `effective_params()` — The Single Source of Truth

```python
from strategy_registry import effective_params

params = effective_params(
    agent_name="ScalpRunner",
    strategy_type="scalp_4step",
    stored=db_config,      # Layer 2: from agent_configs table
    override=test_override, # Layer 3: optional, in-memory only
)
```

Resolution order (later layers override earlier):

1. **Code defaults** — `SCALP_DEFAULT_PARAMS` (or equivalent). These are the baseline values shipped with the codebase.
2. **Stored DB config** — Whatever was saved via the admin UI. Merged with `deep_merge()` (recursive dict merge), so partial updates only override the fields you changed.
3. **Test override** — In-memory only, never persisted. Used by `scalp_experiments.py` to test parameter variations without touching the database.

The result is a single validated dict containing all strategy params, risk controls, and metadata.

### `deep_merge()` — Recursive Dict Merge

```python
# Base: {"exit_rules": {"sl_pct": 1.0, "tp_pct": 2.0}}
# Stored: {"exit_rules": {"sl_pct": 1.5}}
# Result: {"exit_rules": {"sl_pct": 1.5, "tp_pct": 2.0}}  ← tp_pct preserved
```

This means you can update a single field (e.g. `exit_rules.sl_pct`) via PATCH without sending the entire config.

### Validation

After merging, `validate_params()` checks:
- Risk control ranges (e.g. `risk_per_trade_pct` must be 0.01–5.0)
- Strategy field ranges against the schema (e.g. `sl_atr_multiple` must be 0.1–5.0)
- Enum values (e.g. `direction_mode` must be `"both"`, `"long"`, or `"short"`)
- Type correctness

Invalid configs raise `ValueError` and are rejected by the API with HTTP 422.

---

## 2. Schema System (Admin UI)

### `get_config_schema()` — UI Rendering

```python
from strategy_registry import get_config_schema

schema = get_config_schema("ScalpRunner", "scalp_4step")
# Returns:
# {
#   "schema_name": "scalp_4step",
#   "display_name": "ScalpRunner — 4-Step Scalp Process",
#   "parity_status": "live_backtest_matched",
#   "shared_fields": { "watchlist": ..., "poll_interval": ... },
#   "strategy_fields": { "exit_rules": ..., "order": ..., ... },
#   "risk_controls": { "paper_only": ..., "risk_per_trade_pct": ..., ... }
# }
```

The admin UI fetches this schema via `GET /api/agents/manage/{agent_id}/config-schema` and renders a form with appropriate input types, labels, min/max validation, and dropdown choices.

### Field Types

Each field is defined with `_field(label, type, min, max, choices, default)`:

| Type | UI Rendering | Example |
|---|---|---|
| `number` | Numeric input with min/max | `_field("SL ATR Multiple", "number", minimum=0.1, maximum=5, default=1.5)` |
| `bool` | Toggle switch | `_field("Pre-Move Filter Enabled", "bool", default=True)` |
| `enum` | Dropdown | `_field("Direction Mode", "enum", choices=["both", "long", "short"], default="short")` |
| `list` | Comma-separated list editor | `_field("Scanner Universe", "list", default=[])` |
| `text` | Free text input | `_field("Regime Symbol", "text", default="SPY")` |
| `number_or_null` | Numeric input with "none" option | `_field("Max Position $ Cap", "number_or_null", default=None)` |

### Schema Sections (ScalpRunner)

The ScalpRunner schema has 19 strategy field sections plus shared fields and risk controls:

| Section | Fields | Purpose |
|---|---|---|
| `entry_criteria` | 9 | Entry qualification thresholds (signals, spread, volume, direction) |
| `exit_rules` | 18 | Stop loss, take profit, trailing, stagnation, exit mode, reentry cooldown, adaptive exit phases |
| `position_sizing` | 8 | Max positions, sizing %, consecutive loss rules, final stretch threshold |
| `timeframes` | 4 | Entry/pattern/trend intervals, lookback bars |
| `levels` | 6 | Fibonacci ratios, S/R detection params, breakout confirmation |
| `discovery` | 25 | Movers, news, scanner settings, symbol universes, catalyst tagging |
| `order` | 9 | Order type, offset, expiry, ATR multiples, price precision, market type |
| `indicators` | 20 | RSI, MACD, EMA, ATR, BB, candle body, tape reading (bar velocity, volume acceleration) |
| `cycle_timing` | 3 | Poll interval min/default/max |
| `premove_filter` | 3 | Pre-move cap filter (reject setups with excessive prior movement) |
| `market_regime` | 10 | SPY daily EMA regime filter, adaptive direction (long in bull, short in bear) |
| `breakout_detection` | 2 | Approaching and consolidation thresholds |
| `pattern_detection` | 12 | Pattern recognition params (range breakout, flag, wedge) |
| `liquidity_scoring` | 6 | Liquidity score weights and verdict thresholds |
| `trend_detection` | 3 | RSI bullish/bearish thresholds, max signal count |
| `scoring_weights` | 5 | Composite score weights (confluence, levels, pattern, liquidity, volume) |
| `scoring_thresholds` | 9 | Min qualification score, Fib distance thresholds, alignment scores |
| `technical` | 6 | ATR fallback, swing window, min bars, S/R normalization |
| `data_fetch` | 6 | History periods and min bars per interval type |

### API Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/agents/manage/{id}/config-schema` | Fetch schema for UI rendering |
| `GET` | `/api/agents/manage/{id}/strategy-params` | Fetch current effective params |
| `PUT` | `/api/agents/manage/{id}/strategy-params` | Replace all strategy params |
| `PATCH` | `/api/agents/manage/{id}/strategy-params` | Partial update (deep merge) |
| `GET` | `/api/agents/config-backups` | List all JSON backups |
| `GET` | `/api/agents/manage/{id}/config-backup` | Check backup health (ok/stale/missing) |

Both PUT and PATCH:
1. Merge with existing DB config
2. Validate via `effective_params()` (raises 422 on invalid)
3. Persist to `agent_configs` table
4. Write JSON backup via `backup_agent_config()`

---

## 3. JSON Backup System

### How It Works

**File**: `service/server/config_backup.py`

Every time strategy params are saved (PUT or PATCH), the system writes a JSON backup file containing the **effective configuration** — not the raw DB row. This means the backup reflects exactly what the live runner will use, after all defaults, overrides, and validation.

### Backup File Location

```
<project_root>/config/agents/<sanitized_agent_name>.json
```

Example: `config/agents/scalprunner.json`

The agent name is sanitized (lowercase, spaces → underscores, special chars removed).

### Backup File Format

```json
{
  "agent_name": "ScalpRunner",
  "agent_id": 1,
  "exported_at": "2025-01-15T12:30:00.000000+00:00",
  "schema_name": "scalp_4step",
  "display_name": "ScalpRunner — 4-Step Scalp Process",
  "parity_status": "live_backtest_matched",
  "effective_strategy_params": {
    "entry_criteria": { ... },
    "exit_rules": { ... },
    "position_sizing": { ... },
    ...all 23 sections...
  },
  "shared_config": { ... },
  "content_hash": "209373c1ef90df7d"
}
```

### Key Properties

| Property | How |
|---|---|
| **Atomic writes** | Temp file → `fsync()` → `os.replace()`. A crash mid-write cannot corrupt the existing backup. |
| **Content hash** | SHA-256 of all fields except `exported_at`. Used to detect drift between DB and backup. |
| **Secret redaction** | Any key matching `password\|token\|secret\|api_key\|apikey\|credential\|auth` is replaced with `***REDACTED***`. |
| **No trade data** | Excludes positions, trade history, session state, database internals. |
| **Deterministic** | Same config → same hash. Useful for diffing configs across environments. |

### Backup Health Check

`GET /api/agents/manage/{id}/config-backup` compares the DB's current effective config with the JSON backup:

| Status | Meaning |
|---|---|
| `ok` | DB hash == file hash. Backup is current. |
| `stale` | DB hash != file hash. Config was changed but backup not yet written (should not happen — backup is synchronous). |
| `missing` | No backup file exists (first run, or file was deleted). |
| `malformed` | Backup file exists but is invalid JSON or missing fields. |

### Restore (Programmatic)

```python
from config_backup import restore_agent_config

params = restore_agent_config("ScalpRunner")
# Returns the effective_strategy_params dict, or None if no backup exists.
# Does NOT write to the database — caller must use the PATCH endpoint.
```

The restore function:
- Reads the JSON backup file
- Validates it contains `effective_strategy_params` as a dict
- Rejects files containing secret-like keys (defensive)
- Returns the params dict for the caller to PATCH into the DB

> **Note**: The restore function is available in `config_backup.py` but not yet wired to an API endpoint. To restore, call the function programmatically and then PATCH the result via the admin API.

### Listing All Backups

```python
from config_backup import list_backups

for backup in list_backups():
    print(f"{backup['filename']} — {backup['agent_name']} — hash={backup['content_hash']}")
```

---

## 4. ScalpRunner Parameter Reference

### Winning Configuration (`cap2_spy10`)

The current production config, validated via backtesting to be profitable across all market regimes:

| Section | Key Fields | Values |
|---|---|---|
| `entry_criteria` | `direction_mode` | `"adaptive"` (SPY regime-driven) |
| `exit_rules` | `trailing_sl_pct` / `trailing_activation_pct` | 0.4 / 0.5 |
| `order` | `sl_atr_multiple` / `tp_atr_multiple` | 1.5 / 2.5 |
| `order` | `order_expiry_minutes` | 180 |
| `premove_filter` | `enabled` / `max_move_pct` / `lookback_bars` | True / 2.0 / 8 |
| `market_regime` | `enabled` / `daily_ema_period` | True / 10 |
| `market_regime` | `adaptive_direction` | True (long in bull, short in bear) |
| `market_regime` | `block_shorts_in_bull` | True |
| `indicators` | `tape_reading.enabled` | True (bar velocity + vol acceleration) |
| `exit_rules` | `adaptive_exit` | True (phase-based: 15/45 min thresholds) |
| `discovery` | `catalyst.enabled` | True (news catalyst boost/penalty) |

### All Parameter Sections

#### `entry_criteria` (9 fields)
Entry qualification thresholds.

| Field | Type | Default | Description |
|---|---|---|---|
| `min_signals` | number | 3 | Minimum indicator signals to qualify |
| `min_signal_families` | number | 2 | Minimum distinct signal families |
| `min_vol_ratio` | number | 1.5 | Minimum volume ratio vs average |
| `max_spread_pct` | number | 0.15 | Maximum bid-ask spread % |
| `min_dollar_volume` | number | 1,000,000 | Minimum 20-bar avg dollar volume |
| `min_depth_dollars` | number | 50,000 | Minimum L2 depth in dollars |
| `require_trend_agreement` | bool | True | Require multi-TF trend alignment |
| `block_on_obv_divergence` | bool | True | Block entries on OBV divergence |
| `direction_mode` | enum | `"adaptive"` | `"both"`, `"long"`, `"short"`, or `"adaptive"` (SPY regime-driven) |

#### `exit_rules` (12 fields)
Position exit management.

| Field | Type | Default | Description |
|---|---|---|---|
| `stop_loss_pct` | number | -1.0 | Hard stop loss % (negative) |
| `take_profit_pct` | number | 1.5 | Take profit % |
| `trailing_sl_pct` | number | 0.4 | Trailing stop distance % |
| `trailing_activation_pct` | number | 0.5 | Profit % to activate trailing |
| `stagnation_minutes` | number | 10 | Minutes with no movement before exit |
| `stagnation_threshold_pct` | number | 0.1 | Stagnation movement threshold % |
| `momentum_death_vol_ratio` | number | 0.5 | Vol ratio below which momentum is "dead" |
| `momentum_death_grace_bars` | number | 5 | Grace bars before momentum death exit |
| `ob_exhaustion_rsi` | number | 78 | RSI level for overbought exhaustion exit |
| `exit_mode` | enum | `"set_and_forget"` | `"set_and_forget"` or `"active"` |
| `reentry_cooldown_cycles` | number | 3 | Cycles to wait before re-entering a symbol |
| `default_rsi` | number | 50 | Fallback RSI when no data available |
| `adaptive_exit` | bool | True | Enable phase-based adaptive exit (wide→tight→stagnation) |
| `phase1_minutes` | number | 15 | Phase 1 duration — wide stop, no trailing |
| `phase1_sl_atr_multiple` | number | 1.5 | Phase 1 stop = N × ATR (loose) |
| `phase2_minutes` | number | 45 | Phase 2 start — tighten stop, activate trailing |
| `phase2_sl_atr_multiple` | number | 1.0 | Phase 2 stop = N × ATR (tighter) |
| `phase2_trailing_activation_pct` | number | 0.4 | Profit % to activate trailing in phase 2 |
| `phase3_sl_atr_multiple` | number | 0.5 | Phase 3 stop = N × ATR (very tight) |
| `phase3_stagnation_exit` | bool | True | Exit on stagnation in phase 3 |

#### `position_sizing` (8 fields)
Position size calculation.

| Field | Type | Default | Description |
|---|---|---|---|
| `max_positions` | number | 3 | Maximum concurrent positions |
| `max_pending_orders` | number | 5 | Maximum pending stop-limit orders |
| `normal_sizing_min_pct` | number | 5 | Normal sizing range min % of equity |
| `normal_sizing_max_pct` | number | 10 | Normal sizing range max % of equity |
| `risk_per_trade_pct` | number | 0.25 | Risk per trade as % of equity |
| `consecutive_loss_threshold` | number | 3 | Losses before size cut kicks in |
| `consecutive_loss_size_cut_pct` | number | 50 | Size reduction % after loss streak |
| `final_stretch_threshold_pct` | number | 80.0 | Goal progress % that triggers final stretch |

#### `timeframes` (4 fields)
Multi-timeframe intervals.

| Field | Type | Default | Description |
|---|---|---|---|
| `entry_interval` | enum | `"1m"` | Entry timing interval |
| `pattern_interval` | enum | `"5m"` | Pattern detection interval |
| `trend_interval` | enum | `"15m"` | Trend confirmation interval |
| `lookback_bars` | number | 200 | Bars of history to fetch |

#### `levels` (6 fields)
Fibonacci and support/resistance detection.

| Field | Type | Default | Description |
|---|---|---|---|
| `fib_retracement` | list | [0.382, 0.5, 0.618, 0.786] | Fib retracement ratios |
| `fib_extension` | list | [1.272, 1.618] | Fib extension ratios |
| `sr_lookback_bars` | number | 50 | Bars to scan for S/R levels |
| `sr_min_touches` | number | 2 | Min touches to form a level |
| `sr_tolerance_pct` | number | 0.15 | Clustering tolerance % |
| `breakout_confirm_bars` | number | 3 | Bars to confirm consolidation near level |

#### `discovery` (19 fields)
Symbol discovery pipeline.

| Field | Type | Default | Description |
|---|---|---|---|
| `movers_enabled` | bool | True | Use Schwab/Alpaca movers feed |
| `movers_indices` | list | ["$COMPX", "$DJI", "$SPX"] | Index symbols for movers |
| `news_enabled` | bool | True | Extract tickers from news |
| `news_lookback_hours` | number | 4 | Hours of news to scan |
| `scanner_enabled` | bool | True | Volume/price scanner on universe |
| `scanner_min_vol_ratio` | number | 2.0 | Min vol ratio for scanner |
| `scanner_min_price_change_pct` | number | 0.5 | Min price change % for scanner |
| `scanner_universe_size` | number | 100 | Max symbols from universe to scan |
| `max_shortlist` | number | 15 | Max symbols after discovery |
| `scanner_universe` | list | [32 symbols] | Full scanner universe |
| `fallback_shortlist` | list | [8 symbols] | Fallback if discovery fails |
| `scanner_interval` | enum | `"5m"` | Scanner bar interval |
| `scanner_lookback_bars` | number | 50 | Scanner history bars |
| `scanner_min_bars` | number | 20 | Min bars for scanner analysis |
| `scanner_vol_lookback_bars` | number | 20 | Bars for volume average |
| `news_limit` | number | 50 | Max news items to fetch |
| `news_process_limit` | number | 50 | Max news items to process |
| `news_max_ticker_length` | number | 5 | Max ticker char length |
| `news_max_symbols` | number | 20 | Max symbols from news |
| `catalyst.enabled` | bool | True | Enable catalyst-based score boost/penalty |
| `catalyst.fresh_window_hours` | number | 4 | Hours to consider a catalyst "fresh" |
| `catalyst.min_confidence` | number | 0.60 | Min tag confidence to apply boost/penalty |
| `catalyst.bullish_boost` | number | 1.5 | Score multiplier when catalyst aligns with direction |
| `catalyst.bearish_penalty` | number | 0.5 | Score multiplier when catalyst opposes direction |
| `catalyst.no_catalyst_penalty` | number | 0.9 | Score multiplier when no catalyst present |

#### `order` (9 fields)
Order construction.

| Field | Type | Default | Description |
|---|---|---|---|
| `stop_limit_offset_pct` | number | 0.02 | Limit price offset from stop % |
| `entry_trigger_offset_pct` | number | 0.08 | Entry trigger offset from breakout % |
| `order_expiry_minutes` | number | 180 | Order TTL in minutes |
| `sl_atr_multiple` | number | 1.5 | Stop loss = N × ATR |
| `tp_atr_multiple` | number | 2.5 | Take profit = N × ATR |
| `market_type` | text | `"us-stock"` | Market type for order routing |
| `order_type` | text | `"stop_limit"` | Order type |
| `price_decimals` | number | 6 | Decimal places for price rounding |
| `default_stop_distance_pct` | number | 1.0 | Fallback stop distance when SL not set |

#### `indicators` (14 fields)
Technical indicator parameters.

| Field | Type | Default | Description |
|---|---|---|---|
| `rsi_period` | number | 14 | RSI period |
| `rsi_bullish` | number | 55 | RSI bullish threshold |
| `rsi_overbought` | number | 75 | RSI overbought level |
| `rsi_oversold` | number | 25 | RSI oversold level |
| `macd_fast` | number | 12 | MACD fast EMA period |
| `macd_slow` | number | 26 | MACD slow EMA period |
| `macd_signal` | number | 9 | MACD signal EMA period |
| `ema_periods` | list | [9, 21, 55] | EMA periods to compute |
| `atr_period` | number | 14 | ATR period |
| `bb_squeeze_ratio` | number | 0.6 | Bollinger Band squeeze threshold |
| `candle_body_conviction` | number | 0.6 | Min body/range for conviction candle |
| `candle_body_doji` | number | 0.3 | Max body/range for doji |
| `vol_ratio_bullish` | number | 2.0 | Volume ratio considered bullish |
| `vol_ratio_dead` | number | 0.5 | Volume ratio considered dead |
| `tape_reading.enabled` | bool | True | Enable bar velocity + volume acceleration signals |
| `tape_reading.velocity_lookback` | number | 5 | Bars to measure bar velocity |
| `tape_reading.velocity_threshold` | number | 1.5 | Velocity ratio to flag as "surging" |
| `tape_reading.vol_accel_lookback` | number | 10 | Bars to measure volume acceleration |
| `tape_reading.vol_accel_threshold` | number | 1.8 | Vol ratio to flag as "accelerating" |
| `tape_reading.velocity_weight` | number | 0.05 | Score weight for velocity component |
| `tape_reading.vol_accel_weight` | number | 0.05 | Score weight for volume acceleration component |

#### `premove_filter` (3 fields)
Reject setups where the stock already moved too far before entry.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | True | Enable the filter |
| `max_move_pct` | number | 2.0 | Max acceptable pre-move % |
| `lookback_bars` | number | 8 | Bars to measure pre-move |

#### `market_regime` (6 fields)
SPY daily EMA regime filter.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | True | Enable the filter |
| `symbol` | text | `"SPY"` | Symbol for regime detection |
| `daily_ema_period` | number | 10 | Daily EMA period |
| `block_shorts_in_bull` | bool | True | Block shorts when SPY > EMA |
| `block_longs_in_bear` | bool | False | Block longs when SPY < EMA |
| `threshold_pct` | number | 0.0 | Distance from EMA to trigger (%) |
| `adaptive_direction` | bool | True | Enable adaptive direction mode (long in bull, short in bear) |
| `adaptive_long_in_bull` | bool | True | Go long-only when SPY regime is bull |
| `adaptive_short_in_bear` | bool | True | Go short-only when SPY regime is bear |
| `adaptive_both_in_neutral` | bool | True | Allow both directions when regime is neutral |

#### `breakout_detection` (2 fields)
| Field | Type | Default | Description |
|---|---|---|---|
| `approaching_threshold_pct` | number | 0.5 | Distance % to consider "approaching" a level |
| `consolidation_threshold_pct` | number | 1.0 | Range % to consider "consolidating" near a level |

#### `pattern_detection` (12 fields)
Chart pattern recognition parameters.

| Field | Type | Default | Description |
|---|---|---|---|
| `min_bars` | number | 20 | Min bars for pattern detection |
| `consolidation_lookback` | number | 3 | Bars for consolidation breakout |
| `range_breakout_confidence` | number | 0.7 | Confidence for range breakout |
| `flag_min_bars` | number | 15 | Min bars for flag pattern |
| `flag_strong_move_bars` | number | 5 | Bars measuring the strong move |
| `flag_consolidation_bars` | number | 10 | Bars measuring the consolidation |
| `flag_min_move_pct` | number | 1.5 | Min move % for flag |
| `flag_max_consolidation_range_pct` | number | 1.0 | Max consolidation range % for flag |
| `flag_confidence` | number | 0.6 | Confidence for flag pattern |
| `wedge_min_bars` | number | 20 | Min bars for wedge |
| `wedge_lookback` | number | 20 | Lookback for wedge detection |
| `wedge_confidence` | number | 0.5 | Confidence for wedge pattern |

#### `liquidity_scoring` (6 fields)
| Field | Type | Default | Description |
|---|---|---|---|
| `avg_bars` | number | 20 | Bars for avg price/volume |
| `spread_weight` | number | 0.4 | Spread score weight |
| `depth_weight` | number | 0.3 | Depth score weight |
| `volume_weight` | number | 0.3 | Volume score weight |
| `good_threshold` | number | 0.6 | Composite score for "good" verdict |
| `marginal_threshold` | number | 0.3 | Composite score for "marginal" verdict |

#### `trend_detection` (3 fields)
| Field | Type | Default | Description |
|---|---|---|---|
| `rsi_bullish` | number | 55 | RSI threshold for bullish trend signal |
| `rsi_bearish` | number | 45 | RSI threshold for bearish trend signal |
| `max_signals` | number | 4 | Max trend signals for strength normalization |

#### `scoring_weights` (5 fields)
Composite setup score weights (should sum to 1.0).

| Field | Type | Default | Description |
|---|---|---|---|
| `confluence_weight` | number | 0.30 | Multi-TF confluence weight |
| `level_alignment_weight` | number | 0.25 | Fib/S/R alignment weight |
| `pattern_weight` | number | 0.20 | Pattern confidence weight |
| `liquidity_weight` | number | 0.15 | Liquidity score weight |
| `volume_momentum_weight` | number | 0.10 | Volume momentum weight |

#### `scoring_thresholds` (9 fields)
| Field | Type | Default | Description |
|---|---|---|---|
| `min_qualification_score` | number | 4.0 | Min composite score (0-10) to qualify |
| `fib_near_threshold_pct` | number | 0.5 | Fib distance for "near" alignment |
| `fib_medium_threshold_pct` | number | 1.0 | Fib distance for "medium" alignment |
| `fib_near_score` | number | 0.7 | Alignment score for near Fib |
| `fib_medium_score` | number | 0.3 | Alignment score for medium Fib |
| `level_alignment_ready` | number | 1.0 | Alignment score when ready to break |
| `level_alignment_approaching` | number | 0.5 | Alignment score when approaching |
| `confluence_max` | number | 2.0 | Max confluence score for normalization |
| `score_scale` | number | 10.0 | Multiplier to convert 0-1 to 0-10 scale |

#### `technical` (6 fields)
| Field | Type | Default | Description |
|---|---|---|---|
| `atr_fallback_pct` | number | 0.2 | ATR as % of price when ATR is zero |
| `min_confluence_for_agreement` | number | 2 | Min confluence for trend agreement |
| `swing_window` | number | 2 | Bars on each side for fractal detection |
| `swing_min_bars` | number | 5 | Min bars for swing detection |
| `min_bars_precompute` | number | 30 | Min bars for indicator precomputation |
| `sr_strength_normalization` | number | 10.0 | Divisor for S/R strength normalization |

#### `data_fetch` (6 fields)
| Field | Type | Default | Description |
|---|---|---|---|
| `intraday_period` | enum | `"5d"` | yfinance period for intraday bars |
| `daily_period` | enum | `"3mo"` | yfinance period for daily bars |
| `default_period` | enum | `"1mo"` | yfinance period for other intervals |
| `intraday_min_bars` | number | 30 | Min bars for intraday data |
| `daily_min_bars` | number | 10 | Min bars for daily data |
| `entry_min_bars` | number | 30 | Min 1m bars for entry analysis |

#### `cycle_timing` (3 fields)
| Field | Type | Default | Description |
|---|---|---|---|
| `poll_interval_default` | number | 15 | Default poll interval (seconds) |
| `poll_interval_min` | number | 5 | Minimum poll interval |
| `poll_interval_max` | number | 60 | Maximum poll interval |

---

## 5. How to Change a Strategy Parameter

### Via the Admin UI

1. Navigate to the agent's config page
2. The UI renders all fields from the schema with current values
3. Edit the desired field(s)
4. Save — this triggers a PATCH request, which:
   - Deep-merges the change with existing config
   - Validates the result
   - Persists to the database
   - Writes a JSON backup

### Via the API

```bash
# Partial update (recommended)
curl -X PATCH http://localhost:8000/api/agents/manage/1/strategy-params \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"exit_rules": {"trailing_sl_pct": 0.5}}'

# Full replace (overwrites all strategy params)
curl -X PUT http://localhost:8000/api/agents/manage/1/strategy-params \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"exit_rules": {"trailing_sl_pct": 0.5, "take_profit_pct": 2.0, ...}}'
```

### Via Code (for testing)

```python
from strategy_registry import effective_params

# Test a parameter change without touching the DB
params = effective_params("ScalpRunner", "scalp_4step",
    override={"exit_rules": {"trailing_sl_pct": 0.6}})
# params["exit_rules"]["trailing_sl_pct"] == 0.6
# params["exit_rules"]["take_profit_pct"] == 1.5  ← still from defaults
```

---

## 6. Adding a New Configurable Parameter

If you need to add a new strategy parameter that doesn't exist yet:

1. **Add to defaults** in `agents/scalp_scan_core.py` (or equivalent):
   ```python
   SCALP_DEFAULT_PARAMS = {
       "exit_rules": {
           ...
           "my_new_param": 42,
       },
   }
   ```

2. **Add to schema** in `agents/strategy_registry.py`:
   ```python
   "exit_rules": {
       ...
       "my_new_param": _field("My New Param", "number", minimum=0, maximum=100, default=42),
   },
   ```

3. **Use in code** — read from params, never hardcode:
   ```python
   exit_cfg = params.get("exit_rules", {})
   my_val = float(exit_cfg.get("my_new_param", 42))
   ```

4. **Test** — verify `effective_params()` returns the new field and the UI schema includes it.

The JSON backup will automatically include the new field on the next config save.

---

## 5. ORB Options Parameter Reference

The ORB Options strategy is a standalone research backtester (not yet integrated into the live platform's 3-layer config model). Parameters are passed directly to the backtester via CLI flags or Python dicts. See `docs/ORB_OPTIONS_STRATEGY.md` for full strategy documentation.

### Winning Configuration (`orb_bs_otm1`)

Validated via IV sensitivity (PASS), walk-forward (PASS), and bear market simulation (MIXED). Backtest period: 2026-04-01 → 2026-08-16.

| Parameter | Value | Description |
|---|---|---|
| `range_minutes` | 5 | Opening range window (9:30–9:35 ET) |
| `stop_pct` | 1.0% | Stop loss distance on underlying |
| `target_pct` | 1.5% | Profit target distance on underlying |
| `latest_entry` | "10:30" | No new entries after this time |
| `max_positions` | 3 | Maximum concurrent positions |
| `position_pct` | 10.0% | % of equity per trade (option premium) |
| `strike_offset` | +1 | OTM strike offset from ATM |
| `dte_min` | 2 | Minimum days to expiration |
| `dte_max` | 14 | Maximum days to expiration |
| `option_slippage_bps` | 10 | Option slippage in bps (0.1%) |
| `confirmation_minutes` | 10 | Minutes before stops are checked |
| `circuit_breaker` | 3 | Consecutive losses before halting a symbol |
| `risk_free_rate` | 0.05 | 5% risk-free rate for BS pricing |
| `min_entry_time` | "09:30" | Skip entries before this time |

### Backtest Results

| Metric | Value |
|---|---|
| Total return | +147.37% |
| Profit factor | 1.259 |
| Win rate | 45% |
| Max drawdown | 34.3% |
| Total trades | 354 |
| Symbols | NVDA, TSLA, AAPL, COIN |

### Validation Summary

| Test | Result | Key Finding |
|---|---|---|
| IV sensitivity | PASS | Profitable across 25%–75% IV (0.5x–1.5x) |
| Walk-forward | PASS | 3/3 OOS windows positive, +68.50% compounded |
| Bear market | MIXED | Profitable in both regimes, not regime-specific |

### Running the Backtest

```bash
cd agents
source ../.venv/bin/activate

python3 ../research/strategy_search/orb_options_bs_backtester.py \
  --symbols NVDA,TSLA,AAPL,COIN \
  --start 2026-04-01 --end 2026-08-16 \
  --strike-offset 1 --position-pct 10 \
  --stop-pct 1.0 --target-pct 1.5 \
  --confirmation-min 10 --circuit-breaker 3 \
  --no-iv-fetch
```

### Running the Validation Suite

```bash
python3 ../research/strategy_search/orb_options_validation.py --test all
```
