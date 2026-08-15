#!/usr/bin/env python3
"""Walk-forward experiments for Intraday Compression Expansion."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "agents"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from compression_backtester import CompressionBacktester
from strategy_lab import deep_merge
from strategy_walk_forward import (
    DEFAULT_END,
    DEFAULT_START,
    build_provider,
    run_holdout_split,
    run_slippage_sensitivity,
    run_walk_forward,
    save_results,
)

BASELINE = {
    "entry": {
        "compression_bars": 3,
        "compression_atr_multiple": 0.80,
        "min_inside_bars": 0,
        "require_volume_contraction": False,
        "max_move_from_open_pct": 3.0,
        "vwap_slope_lookback": 3,
        "atr_period": 10,
    },
    "risk": {"stop_buffer_pct": 0.05, "target_multiple_r": 2.0},
}

ABLATIONS = {
    "strict_original": {
        "entry": {"compression_atr_multiple": 0.60, "min_inside_bars": 1, "require_volume_contraction": True}
    },
    "no_vwap_slope": {"entry": {"vwap_slope_lookback": 0}},
    "no_ema_alignment": {"entry": {"ema_fast": 1, "ema_slow": 1}},
    "no_volume_contraction": {"entry": {"require_volume_contraction": False}},
    "no_trend_filter": {"entry": {"vwap_slope_lookback": 0, "ema_fast": 1, "ema_slow": 1}},
}

SWEEP = {
    "comp_2bar": {"entry": {"compression_bars": 2}},
    "comp_3bar": {"entry": {"compression_bars": 3}},
    "comp_4bar": {"entry": {"compression_bars": 4}},
    "comp_040atr": {"entry": {"compression_atr_multiple": 0.40}},
    "comp_060atr": {"entry": {"compression_atr_multiple": 0.60}},
    "comp_080atr": {"entry": {"compression_atr_multiple": 0.80}},
    "inside_1": {"entry": {"min_inside_bars": 1}},
    "window_0945_1100": {"session": {"entry_window_start": "09:45", "entry_window_end": "11:00"}},
    "window_1000_1200": {"session": {"entry_window_end": "12:00"}},
    "target_15r": {"risk": {"target_multiple_r": 1.5}},
}


def _run_group(group: dict, common: dict, provider, label, prefix: str) -> list[dict]:
    results = []
    for name, override in group.items():
        print(f"\n--- {name} ---", file=sys.stderr)
        result = run_walk_forward(CompressionBacktester, deep_merge(BASELINE, override),
                                  slippage_bps=5.0, **common)
        result["candidate_id"] = f"{prefix}_{name}"
        results.append(result)
    return sorted(results, key=lambda item: item.get("total_return_pct", -999), reverse=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compression Expansion walk-forward")
    parser.add_argument("--mode", choices=("reproduce", "ablation", "sweep", "sensitivity", "holdout"), default="reproduce")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--slippage", type=float, default=5.0)
    parser.add_argument("--max-symbols", type=int, default=15)
    parser.add_argument("--json", default="")
    args = parser.parse_args()
    provider, label = build_provider()
    common = dict(start=args.start, end=args.end, max_symbols=args.max_symbols,
                  provider=provider, provider_label=label, strategy_name="compression_expansion")
    if args.mode == "reproduce":
        result = run_walk_forward(CompressionBacktester, BASELINE, slippage_bps=args.slippage, **common)
    elif args.mode == "ablation":
        result = _run_group(ABLATIONS, common, provider, label, "compression")
    elif args.mode == "sweep":
        result = _run_group(SWEEP, common, provider, label, "compression")
    elif args.mode == "sensitivity":
        result = run_slippage_sensitivity(CompressionBacktester, BASELINE, **common)
    else:
        result = run_holdout_split(CompressionBacktester, BASELINE,
                                   start=args.start, end=args.end,
                                   slippage_bps=args.slippage, max_symbols=args.max_symbols,
                                   provider=provider, provider_label=label)
    printable = result if isinstance(result, list) else {k: v for k, v in result.items() if k != "window_details"}
    print(json.dumps(printable, indent=2))
    if args.json:
        save_results(result if isinstance(result, dict) else {"results": result}, args.json)


if __name__ == "__main__":
    main()
