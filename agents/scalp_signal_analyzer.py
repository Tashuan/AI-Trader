"""Forward outcome analysis for ScalpRunner entry signals."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Optional

import pandas as pd

from scalp_scan_backtester import ScalpScanBacktester


class ScalpSignalAnalyzer:
    """Measure forward outcomes for qualifying ScalpRunner setups."""

    def __init__(
        self,
        symbols: list[str],
        params: dict,
        start_date: str = "",
        end_date: str = "",
        provider=None,
        base_interval: str = "5m",
    ):
        self.symbols = symbols
        self.params = params
        self.start_date = start_date
        self.end_date = end_date
        self.provider = provider
        self.base_interval = base_interval
        self.backtester = ScalpScanBacktester(
            symbols=symbols,
            params=params,
            start_date=start_date,
            end_date=end_date,
            provider=provider,
            base_interval=base_interval,
        )

    def run(self, horizons: Optional[list[int]] = None, cooldown_bars: int = 6) -> dict:
        horizons = sorted({int(h) for h in (horizons or [1, 3, 6, 12]) if int(h) > 0})
        if not horizons:
            horizons = [1, 3, 6, 12]
        max_horizon = max(horizons)
        lookback = max(
            int(self.params.get("timeframes", {}).get("lookback_bars", 0) or 0),
            {"1m": 200, "5m": 600, "15m": 400}.get(self.base_interval, 200),
        )
        records: list[dict] = []
        coverage = {}

        for symbol in self.symbols:
            df = self.backtester._fetch(symbol, self.base_interval)
            if df is None or df.empty:
                continue
            coverage[symbol] = len(df)
            time_col = self.backtester._time_col(df)
            last_signal = -10**9
            end_index = len(df) - max_horizon - 1

            for index in range(lookback, max(lookback, end_index + 1)):
                timestamp = df[time_col].iloc[index]
                windows = self.backtester._build_mtf_window({"base": df}, timestamp, lookback)
                if any(window is None for window in windows):
                    continue
                scan = self.backtester._scan_symbol(symbol, *windows)
                setup = scan.get("setup", {}) if scan else {}
                if not setup.get("qualifies") or index - last_signal < cooldown_bars:
                    continue
                last_signal = index
                records.append(self._measure_signal(symbol, df, time_col, index, setup, horizons))

        return self._summarize(records, horizons, coverage)

    def _measure_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        time_col: str,
        index: int,
        setup: dict,
        horizons: list[int],
    ) -> dict:
        entry = float(setup.get("entry_level", 0))
        stop = float(setup.get("sl_level", 0))
        target = float(setup.get("tp_level", 0))
        side = setup.get("direction", "long")
        forward = df.iloc[index + 1:index + 1 + max(horizons)].copy()
        trigger_index = None

        for offset, row in enumerate(forward.itertuples(index=False), start=1):
            high = float(getattr(row, "High"))
            low = float(getattr(row, "Low"))
            triggered = high >= entry if side == "long" else low <= entry
            if triggered:
                trigger_index = offset - 1
                break

        result = {
            "symbol": symbol,
            "timestamp": str(df[time_col].iloc[index]),
            "side": side,
            "entry_price": entry,
            "stop_price": stop,
            "target_price": target,
            "score": setup.get("score", 0),
            "pattern_type": setup.get("pattern_type", "none"),
            "triggered": trigger_index is not None,
            "trigger_delay_bars": (trigger_index + 1) if trigger_index is not None else None,
            "outcome": "no_fill" if trigger_index is None else "unresolved",
            "horizons": {},
        }
        if trigger_index is None:
            return result

        filled = forward.iloc[trigger_index:]
        first_outcome = "unresolved"
        for row in filled.itertuples(index=False):
            high = float(getattr(row, "High"))
            low = float(getattr(row, "Low"))
            stop_hit = low <= stop if side == "long" else high >= stop
            target_hit = high >= target if side == "long" else low <= target
            if stop_hit:
                first_outcome = "stop_first"
                break
            if target_hit:
                first_outcome = "target_first"
                break
        result["outcome"] = first_outcome

        for horizon in horizons:
            window = filled.iloc[:horizon]
            if window.empty:
                continue
            if side == "long":
                mfe = (float(window["High"].max()) - entry) / entry * 100
                mae = (float(window["Low"].min()) - entry) / entry * 100
                close_return = (float(window["Close"].iloc[-1]) - entry) / entry * 100
            else:
                mfe = (entry - float(window["Low"].min())) / entry * 100
                mae = (entry - float(window["High"].max())) / entry * 100
                close_return = (entry - float(window["Close"].iloc[-1])) / entry * 100
            result["horizons"][str(horizon)] = {
                "mfe_pct": round(mfe, 4),
                "mae_pct": round(mae, 4),
                "close_return_pct": round(close_return, 4),
            }
        return result

    @staticmethod
    def _summarize(records: list[dict], horizons: list[int], coverage: dict) -> dict:
        outcome_counts = Counter(record["outcome"] for record in records)
        triggered = [record for record in records if record["triggered"]]
        resolved = [record for record in triggered if record["outcome"] in {"target_first", "stop_first"}]
        wins = sum(record["outcome"] == "target_first" for record in resolved)
        expectancy_r = 0.0
        if resolved:
            values = []
            for record in resolved:
                setup = record
                stop_distance = abs(setup["entry_price"] - setup["stop_price"])
                if stop_distance <= 0:
                    continue
                pnl = setup["target_price"] - setup["entry_price"] if record["side"] == "long" else setup["entry_price"] - setup["target_price"]
                reward_r = abs(pnl) / stop_distance
                values.append(reward_r if record["outcome"] == "target_first" else -1.0)
            expectancy_r = sum(values) / len(values) if values else 0.0

        horizon_stats = {}
        for horizon in horizons:
            values = [r["horizons"][str(horizon)] for r in triggered if str(horizon) in r["horizons"]]
            horizon_stats[str(horizon)] = {
                "samples": len(values),
                "avg_mfe_pct": round(sum(v["mfe_pct"] for v in values) / len(values), 4) if values else 0.0,
                "avg_mae_pct": round(sum(v["mae_pct"] for v in values) / len(values), 4) if values else 0.0,
                "avg_close_return_pct": round(sum(v["close_return_pct"] for v in values) / len(values), 4) if values else 0.0,
            }

        by_symbol = defaultdict(list)
        by_side = defaultdict(list)
        for record in records:
            by_symbol[record["symbol"]].append(record)
            by_side[record["side"]].append(record)
        symbol_stats = {}
        for symbol, symbol_records in by_symbol.items():
            symbol_triggered = [r for r in symbol_records if r["triggered"]]
            symbol_resolved = [r for r in symbol_triggered if r["outcome"] in {"target_first", "stop_first"}]
            symbol_stats[symbol] = {
                "signals": len(symbol_records),
                "triggered": len(symbol_triggered),
                "fill_rate": round(len(symbol_triggered) / len(symbol_records), 4) if symbol_records else 0.0,
                "resolved": len(symbol_resolved),
                "win_rate": round(sum(r["outcome"] == "target_first" for r in symbol_resolved) / len(symbol_resolved), 4) if symbol_resolved else 0.0,
                "outcomes": dict(Counter(r["outcome"] for r in symbol_records)),
            }

        side_stats = {}
        for side, side_records in by_side.items():
            side_resolved = [r for r in side_records if r["outcome"] in {"target_first", "stop_first"}]
            side_stats[side] = {
                "signals": len(side_records),
                "triggered": sum(r["triggered"] for r in side_records),
                "resolved": len(side_resolved),
                "win_rate": round(sum(r["outcome"] == "target_first" for r in side_resolved) / len(side_resolved), 4) if side_resolved else 0.0,
                "outcomes": dict(Counter(r["outcome"] for r in side_records)),
            }

        return {
            "symbols": list(coverage),
            "coverage": coverage,
            "horizons": horizons,
            "per_side": side_stats,
            "signal_count": len(records),
            "triggered_count": len(triggered),
            "fill_rate": round(len(triggered) / len(records), 4) if records else 0.0,
            "resolved_count": len(resolved),
            "resolved_win_rate": round(wins / len(resolved), 4) if resolved else 0.0,
            "expectancy_r": round(expectancy_r, 4),
            "outcomes": dict(outcome_counts),
            "horizon_stats": horizon_stats,
            "per_symbol": symbol_stats,
            "records": records,
            "sample_warning": "Use multiple windows and at least 100 resolved signals before promotion." if len(resolved) < 100 else None,
        }
