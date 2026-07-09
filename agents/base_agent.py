"""
Base Agent Framework

Handles all the boilerplate: registration, login, API calls,
trade execution, signal publishing, heartbeat polling, and
position tracking. Strategy modules inherit from BaseAgent
and implement the `analyze()` and `decide()` methods.
"""

import requests
import time
import json
import logging
import os
import random
from typing import Optional
from dataclasses import dataclass, field

from personality import Personality
from market_data import MarketDataClient, TechnicalSnapshot, NewsItem


# ============================================================
# Trade Decision
# ============================================================

@dataclass
class TradeDecision:
    """A decision made by an agent's strategy."""
    action: str  # "buy", "sell", "short", "cover", "hold"
    symbol: str
    market: str  # "crypto", "us-stock"
    quantity: float
    confidence: float  # 0-1
    reason: str
    publish_strategy: bool = True
    strategy_title: str = ""
    strategy_content: str = ""
    strategy_tags: str = ""


# ============================================================
# Base Agent
# ============================================================

class BaseAgent:
    """Base class for all trading agents."""

    def __init__(
        self,
        personality: Personality,
        api_base: str = "http://localhost:8000/api",
        email: str = "",
        password: str = "",
        poll_interval: int = 60,
    ):
        self.personality = personality
        self.api_base = api_base
        self.email = email or f"{personality.name.lower()}@agent.dev"
        self.password = password or f"{personality.name.lower()}_pass_2026"
        self.poll_interval = poll_interval
        self.token: Optional[str] = None
        self.agent_id: Optional[int] = None
        self.market_data = MarketDataClient(api_base)
        self.positions: list[dict] = []
        self.cash: float = 100000.0
        self.portfolio_value: float = 100000.0
        self.heartbeat_count: int = 0
        self.trades_made: int = 0
        self.signals_published: int = 0
        self.running: bool = False
        self._bought_this_cycle: set[str] = set()
        self._replied_signal_ids: set[int] = set()
        self._discussion_cooldown: int = 0

        self.logger = logging.getLogger(f"Agent:{personality.name}")
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            f"[{personality.name}] %(levelname)s: %(message)s"
        ))
        self.logger.handlers = [handler]
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

    # ============================================================
    # Authentication
    # ============================================================

    def register(self) -> bool:
        """Register a new agent account."""
        try:
            resp = requests.post(f"{self.api_base}/claw/agents/selfRegister", json={
                "name": self.personality.name,
                "email": self.email,
                "password": self.password,
            }, timeout=10)
            if resp.ok:
                data = resp.json()
                self.token = data.get("token")
                self.agent_id = data.get("agent_id")
                self.logger.info(f"Registered as {self.personality.name} (id={self.agent_id})")
                return True
            else:
                self.logger.warning(f"Registration failed: {resp.json()}")
                return False
        except Exception as e:
            self.logger.error(f"Registration error: {e}")
            return False

    def login(self) -> bool:
        """Login with existing credentials."""
        try:
            resp = requests.post(f"{self.api_base}/claw/agents/login", json={
                "name": self.personality.name,
                "password": self.password,
                "client_type": "python_bot",
            }, timeout=10)
            if resp.ok:
                data = resp.json()
                self.token = data.get("token")
                self.agent_id = data.get("agent_id")
                self.logger.info(f"Logged in as {self.personality.name} (id={self.agent_id})")
                return True
            return False
        except Exception as e:
            self.logger.error(f"Login error: {e}")
            return False

    def connect(self) -> bool:
        """Try to login first, then register if needed."""
        if self.login():
            return True
        self.logger.info("Login failed, attempting registration...")
        return self.register()

    @property
    def headers(self) -> dict:
        if not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}"}

    # ============================================================
    # Portfolio Management
    # ============================================================

    def fetch_portfolio(self) -> None:
        """Update current positions and cash balance."""
        try:
            resp = requests.get(f"{self.api_base}/positions", headers=self.headers, timeout=10)
            if resp.ok:
                data = resp.json()
                self.cash = float(data.get("cash") or 0)
                self.positions = data.get("positions", [])
                current_value = 0.0
                for p in self.positions:
                    qty = float(p.get("quantity") or 0)
                    price = float(p.get("current_price") or 0)
                    current_value += qty * price
                self.portfolio_value = self.cash + current_value
        except Exception as e:
            self.logger.error(f"Portfolio fetch error: {e}")

    def get_position(self, symbol: str) -> Optional[dict]:
        """Get current position for a symbol."""
        for p in self.positions:
            if p.get("symbol", "").upper() == symbol.upper():
                return p
        return None

    def position_count(self) -> int:
        return len(self.positions)

    def has_position(self, symbol: str) -> bool:
        return self.get_position(symbol) is not None

    # ============================================================
    # Trade Execution
    # ============================================================

    def execute_trade(self, decision: TradeDecision) -> bool:
        """Execute a trade decision via the platform API."""
        if decision.action == "hold":
            return False

        if self.position_count() >= self.personality.max_positions and decision.action == "buy":
            self.logger.info(f"Max positions reached ({self.personality.max_positions}), skipping {decision.symbol}")
            return False

        if decision.action == "buy" and decision.symbol in self._bought_this_cycle:
            self.logger.info(f"Already bought {decision.symbol} this cycle, skipping")
            return False

        try:
            resp = requests.post(f"{self.api_base}/signals/realtime",
                headers=self.headers,
                json={
                    "market": decision.market,
                    "action": decision.action,
                    "symbol": decision.symbol,
                    "price": 0,
                    "quantity": decision.quantity,
                    "executed_at": "now",
                    "content": decision.reason,
                }, timeout=15)

            if resp.ok:
                data = resp.json()
                price = data.get("price", 0)
                self.logger.info(
                    f"TRADE: {decision.action.upper()} {decision.quantity} {decision.symbol} "
                    f"@ ${price:,.2f} | {decision.reason[:80]}"
                )
                self.trades_made += 1
                if decision.action == "buy":
                    self._bought_this_cycle.add(decision.symbol)
                self.fetch_portfolio()
                return True
            else:
                error = resp.json().get("detail", "unknown")
                self.logger.warning(f"Trade failed: {decision.action} {decision.symbol} - {error}")
                return False
        except Exception as e:
            self.logger.error(f"Trade execution error: {e}")
            return False

    # ============================================================
    # Signal Publishing
    # ============================================================

    def publish_strategy(self, title: str, content: str, market: str = "crypto",
                         symbols: str = "", tags: str = "") -> bool:
        """Publish a strategy signal to the platform."""
        title, content = self.personality.format_signal(title, content)
        try:
            resp = requests.post(f"{self.api_base}/signals/strategy",
                headers=self.headers,
                json={
                    "market": market,
                    "title": title,
                    "content": content,
                    "symbols": symbols,
                    "tags": tags,
                }, timeout=10)
            if resp.ok:
                self.logger.info(f"Published strategy: {title[:60]}")
                self.signals_published += 1
                return True
            return False
        except Exception as e:
            self.logger.error(f"Publish error: {e}")
            return False

    def publish_discussion(self, title: str, content: str, market: str = "crypto",
                           tags: str = "") -> bool:
        """Publish a discussion post."""
        title, content = self.personality.format_signal(title, content)
        try:
            resp = requests.post(f"{self.api_base}/signals/discussion",
                headers=self.headers,
                json={
                    "market": market,
                    "title": title,
                    "content": content,
                    "tags": tags,
                }, timeout=10)
            if resp.ok:
                self.logger.info(f"Published discussion: {title[:60]}")
                self.signals_published += 1
                return True
            return False
        except Exception as e:
            self.logger.error(f"Publish error: {e}")
            return False

    # ============================================================
    # Community Engagement
    # ============================================================

    def fetch_signal_feed(self, message_type: str = "strategy", limit: int = 10) -> list[dict]:
        """Fetch recent signals from the community feed."""
        try:
            resp = requests.get(
                f"{self.api_base}/signals/feed",
                params={"message_type": message_type, "limit": limit, "sort": "new"},
                headers=self.headers,
                timeout=10,
            )
            if resp.ok:
                data = resp.json()
                return data.get("signals", [])
        except Exception as e:
            self.logger.error(f"Feed fetch error: {e}")
        return []

    def reply_to_signal(self, signal_id: int, content: str) -> bool:
        """Reply to another agent's signal."""
        try:
            resp = requests.post(
                f"{self.api_base}/signals/reply",
                headers=self.headers,
                json={"signal_id": signal_id, "content": content},
                timeout=10,
            )
            if resp.ok:
                self.logger.info(f"Replied to signal {signal_id}: {content[:60]}")
                return True
            else:
                self.logger.warning(f"Reply failed: {resp.json().get('detail', 'unknown')}")
        except Exception as e:
            self.logger.error(f"Reply error: {e}")
        return False

    def engage_community(self, max_replies: int = 2) -> None:
        """Read the signal feed and reply to other agents' strategies.
        Also occasionally publishes a discussion post."""
        if not self.token:
            return

        signals = self.fetch_signal_feed(message_type="strategy", limit=15)
        if not signals:
            return

        other_signals = [s for s in signals if s.get("agent_id") != self.agent_id]
        random.shuffle(other_signals)

        replied = 0
        for signal in other_signals:
            if replied >= max_replies:
                break
            signal_id = signal.get("signal_id") or signal.get("id")
            if not signal_id:
                continue
            if signal_id in self._replied_signal_ids:
                continue

            reply_content = self._build_community_reply(signal)
            if not reply_content:
                continue

            if self.reply_to_signal(signal_id, reply_content):
                self._replied_signal_ids.add(signal_id)
                replied += 1

        if replied > 0:
            self.logger.info(f"Community engagement: replied to {replied} signals")

    def publish_market_discussion(self) -> bool:
        """Publish a discussion post with market commentary. Override _build_discussion_topic in subclasses."""
        topic = self._build_discussion_topic()
        if not topic:
            return False
        title, content, market = topic
        return self.publish_discussion(title=title, content=content, market=market, tags="discussion,market-commentary")

    def _build_community_reply(self, signal: dict) -> Optional[str]:
        """Build a reply to another agent's signal. Override in subclasses for personality-specific replies."""
        return None

    def _build_discussion_topic(self) -> Optional[tuple[str, str, str]]:
        """Build a discussion post topic. Override in subclasses. Returns (title, content, market)."""
        return None

    # ============================================================
    # Arena State Reporting
    # ============================================================

    def _report_state(self, state: str, detail: str = "", symbol: str = "", confidence: float = 0.0):
        """Report current state to the arena. Never raises — state reporting is best-effort."""
        if not self.token:
            return
        try:
            requests.post(f"{self.api_base}/arena/state",
                headers=self.headers,
                json={"state": state, "detail": detail, "symbol": symbol, "confidence": confidence},
                timeout=5)
        except Exception:
            pass

    # ============================================================
    # Heartbeat
    # ============================================================

    def heartbeat(self) -> dict:
        """Poll heartbeat for notifications and tasks."""
        try:
            resp = requests.post(f"{self.api_base}/claw/agents/heartbeat",
                headers=self.headers, timeout=10)
            if resp.ok:
                data = resp.json()
                self.heartbeat_count += 1
                messages = data.get("messages", [])
                tasks = data.get("tasks", [])
                if messages:
                    self.logger.info(f"Heartbeat: {len(messages)} messages, {len(tasks)} tasks")
                    for msg in messages:
                        self.logger.info(f"  [{msg.get('type')}] {msg.get('content', '')[:80]}")
                return data
        except Exception as e:
            self.logger.error(f"Heartbeat error: {e}")
        return {}

    # ============================================================
    # Agent Info
    # ============================================================

    def fetch_agent_info(self) -> dict:
        """Fetch current agent info from the platform."""
        try:
            resp = requests.get(f"{self.api_base}/claw/agents/me", headers=self.headers, timeout=10)
            if resp.ok:
                return resp.json()
        except Exception:
            pass
        return {}

    def status_report(self) -> str:
        """Generate a status report string."""
        return (f"{self.personality.name} | Portfolio: ${self.portfolio_value:,.2f} | "
                f"Cash: ${self.cash:,.2f} | Positions: {self.position_count()} | "
                f"Trades: {self.trades_made} | Signals: {self.signals_published} | "
                f"Heartbeats: {self.heartbeat_count}")

    # ============================================================
    # Main Loop - Override these in strategy subclasses
    # ============================================================

    def analyze(self) -> list[TradeDecision]:
        """Override: Analyze market data and return trade decisions."""
        return []

    def on_heartbeat(self, heartbeat_data: dict) -> None:
        """Override: React to heartbeat messages (replies, followers, tasks)."""
        pass

    def on_start(self) -> None:
        """Override: Called once when agent starts."""
        pass

    def on_cycle(self) -> None:
        """Override: Called each cycle before analyze()."""
        pass

    def run(self, max_cycles: int = 0) -> None:
        """Main agent loop."""
        if not self.connect():
            self.logger.error("Failed to connect to platform. Exiting.")
            return

        self.fetch_portfolio()
        self.logger.info(f"Connected. Portfolio: ${self.portfolio_value:,.2f}, Cash: ${self.cash:,.2f}")
        self.on_start()
        self.running = True
        cycle = 0

        while self.running:
            cycle += 1
            self._bought_this_cycle.clear()
            try:
                self._report_state("scanning", "Scanning watchlist", confidence=0.0)
                self.on_cycle()
                self.fetch_portfolio()

                self._report_state("researching", "Analyzing market data")
                decisions = self.analyze()
                for decision in decisions:
                    if decision.publish_strategy and decision.strategy_title:
                        self.publish_strategy(
                            title=decision.strategy_title,
                            content=decision.strategy_content,
                            market=decision.market,
                            symbols=decision.symbol,
                            tags=decision.strategy_tags or "agent,automated",
                        )
                    if decision.action in ("buy", "short"):
                        self._report_state("entering", detail=decision.reason,
                            symbol=decision.symbol, confidence=decision.confidence)
                    elif decision.action in ("sell", "cover"):
                        self._report_state("exiting", detail=decision.reason,
                            symbol=decision.symbol, confidence=decision.confidence)
                    self.execute_trade(decision)

                self._report_state("reviewing", "Reviewing portfolio and community")
                hb = self.heartbeat()
                if hb:
                    self.on_heartbeat(hb)

                self.engage_community(max_replies=2)

                self._discussion_cooldown -= 1
                if self._discussion_cooldown <= 0:
                    if self.publish_market_discussion():
                        self._discussion_cooldown = 5
                    else:
                        self._discussion_cooldown = 1

                self.logger.info(f"Cycle {cycle} complete. {self.status_report()}")

            except KeyboardInterrupt:
                self.logger.info("Stopping agent...")
                break
            except Exception as e:
                self.logger.error(f"Cycle error: {e}")

            if max_cycles > 0 and cycle >= max_cycles:
                self.logger.info(f"Reached max cycles ({max_cycles}). Stopping.")
                break

            self._report_state("idle", "Waiting for next cycle")
            time.sleep(self.poll_interval)

    def stop(self) -> None:
        self.running = False
