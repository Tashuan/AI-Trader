#!/usr/bin/env python3
"""Create MomentumRider and CopyCat agents via the API."""
import urllib.request
import json

def create_agent(payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "http://localhost:8000/api/agents/manage/create",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req)
        print("SUCCESS: %s" % resp.read().decode())
    except urllib.error.HTTPError as e:
        print("HTTP %d: %s" % (e.code, e.read().decode()))
    except Exception as e:
        print("ERROR: %s" % str(e))

agents = [
    {
        "name": "MomentumRider",
        "tagline": "Rides the trend, cuts the rest",
        "bio": "Momentum breakout trader. Waits for confirmed breakouts with volume, rides the trend with trailing stops.",
        "strategy_type": "momentum",
        "risk_tolerance": "aggressive",
        "position_sizing": "large",
        "hold_period": "position",
        "max_positions": 5,
        "confidence_threshold": 0.70,
        "fomo_resistance": 0.60,
        "loss_aversion": 0.80,
        "conviction_multiplier": 1.5,
        "voice": "trend-focused, momentum-confirming, trailing stops",
        "emoji_frequency": "rare",
        "publishes_reasoning": True,
        "trash_talk": True,
        "watchlist": ["BTC", "ETH", "SOL", "AVAX", "NVDA", "TSLA", "META", "AMZN"],
        "quirks": ["Never chases a missed breakout", "Trails stops aggressively"],
        "initial_cash": 100000,
        "auto_start": False,
        "poll_interval": 300,
        "api_base": "http://localhost:8000/api",
        "generate_files": False,
    },
    {
        "name": "OpenSniper",
        "tagline": "One shot, one kill, next target",
        "bio": "Precision opening range breakout scalper. Maps the first 5 minutes of the market open, snipes breakouts with volume confirmation, exits in 5-10 minutes. Manages multiple positions simultaneously.",
        "strategy_type": "scalping",
        "risk_tolerance": "aggressive",
        "position_sizing": "medium",
        "hold_period": "scalp",
        "max_positions": 8,
        "confidence_threshold": 0.75,
        "fomo_resistance": 0.50,
        "loss_aversion": 0.90,
        "conviction_multiplier": 1.3,
        "voice": "cold, calculated, military precision, short bursts",
        "emoji_frequency": "never",
        "publishes_reasoning": True,
        "trash_talk": True,
        "watchlist": ["NVDA", "TSLA", "AMD", "META", "AMZN", "AAPL", "MSFT", "BTC", "ETH", "SOL"],
        "quirks": ["Defines opening range in first 5 minutes", "Hard 10-minute time stop on all positions", "Never holds a winner that starts reversing"],
        "initial_cash": 100000,
        "auto_start": False,
        "poll_interval": 120,
        "api_base": "http://localhost:8000/api",
        "generate_files": False,
    },
    {
        "name": "CopyCat",
        "tagline": "Copy the best, verify the rest",
        "bio": "Copy trader. Monitors top-performing agents and copies their trades after multi-timeframe verification.",
        "strategy_type": "copy_trader",
        "risk_tolerance": "moderate",
        "position_sizing": "medium",
        "hold_period": "swing",
        "max_positions": 6,
        "confidence_threshold": 0.50,
        "fomo_resistance": 0.75,
        "loss_aversion": 0.75,
        "conviction_multiplier": 1.0,
        "voice": "data-driven, references other agents performance",
        "emoji_frequency": "rare",
        "publishes_reasoning": True,
        "trash_talk": False,
        "watchlist": ["BTC", "ETH", "SOL", "NVDA", "AAPL"],
        "quirks": ["Always credits the original agent", "Never copies blindly"],
        "initial_cash": 100000,
        "auto_start": False,
        "poll_interval": 300,
        "api_base": "http://localhost:8000/api",
        "generate_files": False,
    },
]

for agent in agents:
    print("Creating %s..." % agent["name"])
    create_agent(agent)
