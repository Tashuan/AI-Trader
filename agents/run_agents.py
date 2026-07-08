#!/usr/bin/env python3
"""
Agent Runner

Launches multiple AI trading agents that compete on the local
AI-Trader platform. Each agent runs in its own thread with its
own personality and strategy.

Usage:
    python run_agents.py                          # Run all default agents
    python run_agents.py newshound chartmaster    # Run specific agents
    python run_agents.py --list                   # List available agents
    python run_agents.py --cycles 10              # Run for 10 cycles then stop
    python run_agents.py --interval 120           # 120s poll interval
"""

import sys
import os
import argparse
import threading
import time
import signal

# Add agents directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from personality import PERSONALITIES, list_personalities
from base_agent import BaseAgent
from strategy_news import NewsHoundAgent
from strategy_technical import ChartMasterAgent
from strategy_contrarian import FadeMasterAgent
from strategy_momentum import BlitzTraderAgent

# Map personality keys to agent classes
AGENT_CLASSES = {
    "newshound": NewsHoundAgent,
    "chartmaster": ChartMasterAgent,
    "fademaster": FadeMasterAgent,
    "blitztrader": BlitzTraderAgent,
}

# Default agents to run
DEFAULT_AGENTS = ["newshound", "chartmaster", "fademaster", "blitztrader"]


def create_agent(key: str, api_base: str, poll_interval: int) -> BaseAgent:
    """Create an agent instance from a personality key."""
    personality = PERSONALITIES[key]
    agent_class = AGENT_CLASSES.get(key, BaseAgent)
    return agent_class(
        personality=personality,
        api_base=api_base,
        poll_interval=poll_interval,
    )


def run_agent_thread(agent: BaseAgent, max_cycles: int, stop_event: threading.Event):
    """Run an agent in a thread, respecting stop event."""
    if not agent.connect():
        agent.logger.error("Failed to connect. Thread exiting.")
        return

    agent.fetch_portfolio()
    agent.logger.info(f"Portfolio: ${agent.portfolio_value:,.2f}, Cash: ${agent.cash:,.2f}")
    agent.on_start()

    cycle = 0
    while not stop_event.is_set():
        cycle += 1
        agent._bought_this_cycle.clear()
        try:
            agent.fetch_portfolio()
            decisions = agent.analyze()
            for decision in decisions:
                if decision.publish_strategy and decision.strategy_title:
                    agent.publish_strategy(
                        title=decision.strategy_title,
                        content=decision.strategy_content,
                        market=decision.market,
                        symbols=decision.symbol,
                        tags=decision.strategy_tags or "agent,automated",
                    )
                agent.execute_trade(decision)

            hb = agent.heartbeat()
            if hb:
                agent.on_heartbeat(hb)

            # Community engagement — reply to other agents' signals
            agent.engage_community(max_replies=2)

            # Occasionally publish a discussion post (every 5 cycles)
            agent._discussion_cooldown -= 1
            if agent._discussion_cooldown <= 0:
                if agent.publish_market_discussion():
                    agent._discussion_cooldown = 5
                else:
                    agent._discussion_cooldown = 1

            agent.logger.info(f"Cycle {cycle} | {agent.status_report()}")

        except Exception as e:
            agent.logger.error(f"Cycle {cycle} error: {e}")

        if max_cycles > 0 and cycle >= max_cycles:
            agent.logger.info(f"Max cycles reached ({max_cycles}). Stopping.")
            break

        # Sleep in small increments so we can respond to stop signal
        for _ in range(agent.poll_interval):
            if stop_event.is_set():
                break
            time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description="Run AI-Trader agents locally")
    parser.add_argument("agents", nargs="*", default=DEFAULT_AGENTS,
                        help="Agent keys to run (default: all)")
    parser.add_argument("--list", action="store_true", help="List available agents")
    parser.add_argument("--cycles", type=int, default=0, help="Max cycles per agent (0 = infinite)")
    parser.add_argument("--interval", type=int, default=60, help="Poll interval in seconds")
    parser.add_argument("--api", type=str, default="http://localhost:8000/api",
                        help="API base URL")
    args = parser.parse_args()

    if args.list:
        print("\nAvailable agents:")
        for key, info in list_personalities().items():
            print(f"  {key:15s} — {info['name']}: {info['tagline']} [{info['strategy']}]")
        return

    # Validate agent keys
    for key in args.agents:
        if key not in PERSONALITIES:
            print(f"Unknown agent: {key}")
            print(f"Available: {', '.join(PERSONALITIES.keys())}")
            return

    print(f"\n{'='*60}")
    print(f"  AI-Trader Multi-Agent Runner")
    print(f"{'='*60}")
    print(f"  Agents: {', '.join(args.agents)}")
    print(f"  API: {args.api}")
    print(f"  Poll interval: {args.interval}s")
    print(f"  Max cycles: {'infinite' if args.cycles == 0 else args.cycles}")
    print(f"{'='*60}\n")

    stop_event = threading.Event()
    threads = []

    def signal_handler(sig, frame):
        print("\n\nStopping all agents...")
        stop_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    for key in args.agents:
        agent = create_agent(key, args.api, args.interval)
        t = threading.Thread(target=run_agent_thread, args=(agent, args.cycles, stop_event),
                             name=f"Agent-{key}", daemon=True)
        t.start()
        threads.append(t)
        print(f"  Started: {agent.personality.name} ({key})")
        time.sleep(2)  # Stagger starts

    print(f"\n  {len(threads)} agents running. Press Ctrl+C to stop.\n")

    while not stop_event.is_set():
        time.sleep(1)

    for t in threads:
        t.join(timeout=10)

    print("\nAll agents stopped.")


if __name__ == "__main__":
    main()
