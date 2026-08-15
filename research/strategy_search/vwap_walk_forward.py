#!/usr/bin/env python3
"""Walk-forward experiments for VWAP Magnet."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "agents"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from vwap_magnet_backtester import VWAPMagnetBacktester
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
    "entry": {"min_displacement_pct": 0.50},
    "risk": {"stop_buffer_pct": 0.30},
}

SWEEP = {
    "disp_030": {"entry": {"min_displacement_pct": 0.30}},
    "disp_050": {"entry": {"min_displacement_pct": 0.50}},
    "disp_075": {"entry": {"min_displacement_pct": 0.75}},
    "disp_100": {"entry": {"min_displacement_pct": 1.00}},
    "stop_020": {"risk": {"stop_buffer_pct": 0.20}},
    "stop_030": {"risk": {"stop_buffer_pct": 0.30}},
    "stop_050": {"risk": {"stop_buffer_pct": 0.50}},
    "settle_0940": {"session": {"settle_end": "09:40"}},
    "settle_0950": {"session": {"settle_end": "09:50"}},
    "latest_1030": {"session": {"latest_entry": "10:30"}},
    "latest_1200": {"session": {"latest_entry": "12:00"}},
}


def main() -> None:
    parser = argparse.ArgumentParser(description="VWAP Magnet walk-forward")
    parser.add_argument("--mode", choices=("reproduce", "sweep", "sensitivity", "holdout"), default="reproduce")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--slippage", type=float, default=5.0)
    parser.add_argument("--max-symbols", type=int, default=15)
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    provider, label = build_provider()
    common = dict(start=args.start, end=args.end, max_symbols=args.max_symbols,
                  provider=provider, provider_label=label, strategy_name="vwap_magnet")
    if args.mode == "reproduce":
        result = run_walk_forward(VWAPMagnetBacktester, BASELINE,
                                  slippage_bps=args.slippage, **common)
    elif args.mode == "sensitivity":
        result = run_slippage_sensitivity(VWAPMagnetBacktester, BASELINE, **common)
    elif args.mode == "holdout":
        result = run_holdout_split(VWAPMagnetBacktester, BASELINE,
                                   start=args.start, end=args.end,
                                   slippage_bps=args.slippage, max_symbols=args.max_symbols,
                                   provider=provider, provider_label=label)
    else:
        result = []
        for name, override in SWEEP.items():
            print(f"\n--- {name} ---", file=sys.stderr)
            r = run_walk_forward(VWAPMagnetBacktester, override,
                                 slippage_bps=args.slippage, **common)
            r["candidate_id"] = f"vwap_{name}"
            result.append(r)
        result.sort(key=lambda item: item.get("total_return_pct", -999), reverse=True)

    print(json.dumps(result if isinstance(result, list) else {
        key: value for key, value in result.items() if key != "window_details"
    }, indent=2))
    if args.json:
        save_results(result if isinstance(result, dict) else {"results": result}, args.json)


if __name__ == "__main__":
    main()
