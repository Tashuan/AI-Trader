#!/usr/bin/env python3
"""
scalp_scan.py — Top-level entry point for the 4-step scalp scan.

This module re-exports run_scan from the workspace implementation so that
scalp_runner.py and the backtester can import it the same way they import
crypto_scan.py and scan.py.

Usage:
  python3 scalp_scan.py --token $TOKEN          # Full scan
  python3 scalp_scan.py                          # Scan only (defaults)
  python3 scalp_scan.py --symbol NVDA            # Single symbol debug
  python3 scalp_scan.py --config '{"...": ...}'  # Inline config override
"""

import argparse
import json
import os
import sys

# Add workspace dir to path so we can import the workspace scan module
_AGENTS_DIR = os.path.dirname(os.path.abspath(__file__))
_WORKSPACE_DIR = os.path.join(_AGENTS_DIR, "workspaces", "scalprunner")
if _WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, _WORKSPACE_DIR)
if _AGENTS_DIR not in sys.path:
    sys.path.insert(0, _AGENTS_DIR)

# Re-export from workspace implementation
import scalp_scan_core  # noqa: F401, E402
from scalp_scan_core import SCALP_DEFAULT_PARAMS as DEFAULT_PARAMS  # noqa: E402

# Import run_scan from the workspace scan module
sys.path.insert(0, _WORKSPACE_DIR)
import scan as _workspace_scan  # noqa: E402

run_scan = _workspace_scan.run_scan


def main():
    parser = argparse.ArgumentParser(description="ScalpRunner — 4-Step Scalp Scan")
    parser.add_argument("--token", type=str, help="Agent auth token")
    parser.add_argument("--symbol", type=str, help="Single symbol to scan (debug mode)")
    parser.add_argument("--config", type=str, help="Inline JSON config override")
    args = parser.parse_args()

    result = run_scan(token=args.token, inline_config=args.config, symbol=args.symbol)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
