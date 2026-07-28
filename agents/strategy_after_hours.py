"""
NightHawk Agent — After-Hours Session-Aware Scalp Strategy

Trades crypto perpetuals with behavior that shifts by global trading session.
Aggressive during London open (kill zone), patient during dead zones.
Session-relative volume, volatility gate, tiered signal requirements.
"""

from base_agent import BaseAgent, TradeDecision
from market_data import TechnicalSnapshot
from typing import Optional
from datetime import datetime, timezone, timedelta
import random


class NightHawkAgent(BaseAgent):
    """Session-aware after-hours scalper — patient predator, aggressive at the right time."""

    PROFIT_TARGET_PCT = 1.5
    STOP_LOSS_PCT = -1.5
    STAGNATION_TIMEOUT_CYCLES = 6
    STAGNATION_THRESHOLD_PCT = 0.3

    def on_start(self):
        self.logger.info(f"{self.personality.tagline}")
        self.logger.info(f"Watchlist: {self.personality.watchlist}")
        self.publish_strategy(
            title=f"{self.personality.name} is LIVE — the night belongs to me",
            content=(f"{self.personality.bio}\n\n"
                     f"My strategy: I scan {', '.join(self.personality.watchlist)} every "
                     f"{self.poll_interval}s, but I only strike when the session is right. "
                     f"London open is my kill zone — full sizing, highest conviction. "
                     f"Tokyo and London morning are active. US after-hours and weekends, "
                     f"I'm cautious — thin books punish recklessness. "
                     f"Profit target: +{self.PROFIT_TARGET_PCT}%. Stop loss: {self.STOP_LOSS_PCT}%. "
                     f"Patience is a weapon. I hunt, I don't chase."),
            market="crypto",
            symbols=",".join(self.personality.watchlist[:3]),
            tags="introduction,after-hours,scalp,session-aware",
        )

    def analyze(self) -> list[TradeDecision]:
        decisions = []
        session = self._get_current_session()

        for symbol in self.personality.watchlist:
            tech = self.market_data.fetch_technical(symbol)
            if not tech:
                continue

            market = "crypto"

            if self.has_position(symbol):
                exit_decision = self._check_exit(symbol, tech, market)
                if exit_decision:
                    decisions.append(exit_decision)
                    continue

            if not self.has_position(symbol):
                entry_decision = self._check_entry(symbol, tech, market, session)
                if entry_decision:
                    decisions.append(entry_decision)

        self.logger.info(
            f"Scan complete: session={session} | {len(self.personality.watchlist)} symbols | "
            f"{len(decisions)} signals"
        )
        return decisions

    # ============================================================
    # Session Detection
    # ============================================================

    def _get_current_session(self) -> str:
        """Determine the current global trading session based on ET time."""
        now = datetime.now(timezone.utc)
        et_hour = (now + timedelta(hours=-4)).hour  # ET = UTC-4 (simplified, no DST)

        if 8 <= et_hour < 16:
            return "us_day"
        elif 16 <= et_hour < 19:
            return "us_after_hours"
        elif 19 <= et_hour < 24 or 0 <= et_hour < 2:
            return "tokyo"
        elif 2 <= et_hour < 3:
            return "london_pre_open"
        elif 3 <= et_hour < 5:
            return "london_kill_zone"
        elif 5 <= et_hour < 8:
            return "london_morning"
        return "tokyo"

    def _session_signal_threshold(self, session: str) -> int:
        """Minimum signal count by session tier."""
        if session == "london_kill_zone":
            return 3
        elif session in ("tokyo", "london_morning", "us_day"):
            return 4
        else:
            return 5

    def _session_sizing_multiplier(self, session: str) -> float:
        """Position sizing multiplier by session tier."""
        if session == "london_kill_zone":
            return 1.0
        elif session in ("tokyo", "london_morning", "us_day"):
            return 0.6
        elif session == "london_pre_open":
            return 0.0  # Watch-only, no entries
        else:
            return 0.35

    # ============================================================
    # Entry: Session-Aware Momentum Detection
    # ============================================================

    def _check_entry(self, symbol: str, tech: TechnicalSnapshot, market: str, session: str) -> Optional[TradeDecision]:
        """Check for session-aware entry conditions."""
        if session == "london_pre_open":
            return None  # Watch-only session

        signals = self._count_entry_signals(tech)
        threshold = self._session_signal_threshold(session)

        if signals < threshold:
            return None

        if not self._volatility_gate(tech):
            return None

        if tech.volume < tech.avg_volume * 1.5:
            return None

        confidence = self._entry_confidence(tech, session)
        if not self.personality.should_trade(confidence):
            return None

        sizing_mult = self._session_sizing_multiplier(session)
        base_quantity = self.personality.size_position(confidence, self.portfolio_value, tech.price)
        quantity = base_quantity * sizing_mult
        if quantity <= 0:
            return None

        reason = self._build_entry_reason(tech, session, signals)
        return TradeDecision(
            action="buy",
            symbol=symbol,
            market=market,
            quantity=quantity,
            confidence=confidence,
            reason=reason,
            publish_strategy=self.personality.publishes_reasoning,
            strategy_title=f"NIGHTHAWK ENTRY: {symbol} — {session} strike at ${tech.price:,.2f}",
            strategy_content=reason,
            strategy_tags="after-hours,scalp,session-aware,nighthawk",
        )

    def _count_entry_signals(self, tech: TechnicalSnapshot) -> int:
        """Count confirming entry signals across signal families."""
        signals = 0

        # Volume family
        if tech.volume > tech.avg_volume * 1.5:
            signals += 1
        if tech.volume > tech.avg_volume * 2.0:
            signals += 1

        # Trend family
        if tech.price > tech.sma_20:
            signals += 1
        if tech.resistance > 0 and tech.price >= tech.resistance * 0.99:
            signals += 1

        # Momentum family
        if tech.macd_histogram > 0:
            signals += 1
        if 40 < tech.rsi < 70:
            signals += 1
        if 50 < tech.rsi < 65:
            signals += 1

        # Volatility expansion
        if tech.price > tech.bollinger_mid:
            signals += 1

        # Recent returns
        if tech.return_5d is not None and tech.return_5d > 3:
            signals += 1

        return signals

    def _volatility_gate(self, tech: TechnicalSnapshot) -> bool:
        """Check if ATR is abnormally spiked — thin-book whipsaw proxy."""
        if tech.atr <= 0:
            return True
        if tech.avg_atr <= 0:
            return True
        ratio = tech.atr / tech.avg_atr
        if ratio > 2.0:
            return False
        return True

    def _entry_confidence(self, tech: TechnicalSnapshot, session: str) -> float:
        """Calculate confidence from signal strength + session bonus."""
        score = 0.0

        if tech.volume > tech.avg_volume * 2.5:
            score += 0.30
        elif tech.volume > tech.avg_volume * 2.0:
            score += 0.22
        elif tech.volume > tech.avg_volume * 1.5:
            score += 0.15

        if tech.resistance > 0 and tech.price >= tech.resistance * 0.99:
            score += 0.20
        if tech.price > tech.sma_20:
            score += 0.10

        if tech.macd_histogram > 0:
            score += 0.15

        if 50 < tech.rsi < 65:
            score += 0.15
        elif 45 < tech.rsi < 70:
            score += 0.08

        if tech.return_5d is not None and tech.return_5d > 5:
            score += 0.10
        elif tech.return_5d is not None and tech.return_5d > 2:
            score += 0.05

        if session == "london_kill_zone":
            score += 0.10

        return min(score, 1.0)

    # ============================================================
    # Exit: Tighter Targets, Session-Aware
    # ============================================================

    def _check_exit(self, symbol: str, tech: TechnicalSnapshot, market: str) -> Optional[TradeDecision]:
        """Check if any position should be exited."""
        position = self.get_position(symbol)
        qty = float(position.get("quantity", 0))
        if qty <= 0:
            return None

        entry = float(position.get("entry_price", 0))
        pnl = float(position.get("pnl", 0))
        if entry <= 0:
            return None

        pnl_pct = (pnl / (entry * qty)) * 100 if qty > 0 else 0

        if pnl_pct >= self.PROFIT_TARGET_PCT:
            reason = self._build_profit_exit_reason(tech, pnl_pct)
            return TradeDecision(
                action="sell",
                symbol=symbol,
                market=market,
                quantity=qty,
                confidence=0.90,
                reason=reason,
                publish_strategy=self.personality.publishes_reasoning,
                strategy_title=f"NIGHTHAWK OUT: {symbol} +{pnl_pct:.1f}% — target down",
                strategy_content=reason,
                strategy_tags="after-hours,scalp,profit-taking,nighthawk",
            )

        if pnl_pct <= self.STOP_LOSS_PCT:
            reason = self._build_stop_loss_reason(tech, pnl_pct)
            return TradeDecision(
                action="sell",
                symbol=symbol,
                market=market,
                quantity=qty,
                confidence=0.85,
                reason=reason,
                publish_strategy=self.personality.publishes_reasoning,
                strategy_title=f"NIGHTHAWK STOP: {symbol} {pnl_pct:.1f}% — cut",
                strategy_content=reason,
                strategy_tags="after-hours,scalp,stop-loss,nighthawk",
            )

        if tech.rsi > 75:
            reason = (f"Exiting {symbol} at ${tech.price:,.2f} — RSI at {tech.rsi:.0f} "
                      f"screaming overbought. PnL: {pnl_pct:+.1f}%. "
                      f"I don't wait for the reversal in thin hours.")
            return TradeDecision(
                action="sell",
                symbol=symbol,
                market=market,
                quantity=qty,
                confidence=0.75,
                reason=reason,
                publish_strategy=self.personality.publishes_reasoning,
                strategy_title=f"NIGHTHAWK OUT: {symbol} RSI {tech.rsi:.0f} — overbought",
                strategy_content=reason,
                strategy_tags="after-hours,scalp,overbought,exit,nighthawk",
            )

        if tech.volume < tech.avg_volume * 0.5 and pnl_pct > 0:
            reason = (f"Exiting {symbol} at ${tech.price:,.2f} — volume collapsed to "
                      f"{tech.volume / tech.avg_volume:.0%} of average. "
                      f"Momentum is dead. Taking {pnl_pct:+.1f}%. Next.")
            return TradeDecision(
                action="sell",
                symbol=symbol,
                market=market,
                quantity=qty,
                confidence=0.70,
                reason=reason,
                publish_strategy=self.personality.publishes_reasoning,
                strategy_title=f"NIGHTHAWK OUT: {symbol} volume died — momentum over",
                strategy_content=reason,
                strategy_tags="after-hours,scalp,volume-fade,exit,nighthawk",
            )

        return None

    # ============================================================
    # Reason Builders
    # ============================================================

    def _build_entry_reason(self, tech: TechnicalSnapshot, session: str, signal_count: int) -> str:
        vol_mult = tech.volume / tech.avg_volume if tech.avg_volume > 0 else 0
        parts = [
            f"NIGHTHAWK ENTRY on {tech.symbol} at ${tech.price:,.2f}",
            f"Session: {session}",
            f"Signals: {signal_count} (threshold: {self._session_signal_threshold(session)})",
            f"Volume: {vol_mult:.1f}x average",
            f"RSI: {tech.rsi:.1f}",
            f"MACD histogram: {tech.macd_histogram:+.4f}",
            f"Price vs SMA20: {'above' if tech.price > tech.sma_20 else 'below'}",
        ]
        if tech.return_5d is not None:
            parts.append(f"5d return: {tech.return_5d:+.1f}%")
        parts.append(f"Target: +{self.PROFIT_TARGET_PCT}%. Stop: {self.STOP_LOSS_PCT}%.")
        return " | ".join(parts)

    def _build_profit_exit_reason(self, tech: TechnicalSnapshot, pnl_pct: float) -> str:
        return (f"Taking profits on {tech.symbol} at ${tech.price:,.2f}. "
                f"Up {pnl_pct:+.1f}% — target hit. "
                f"RSI={tech.rsi:.0f}, volume {'still high' if tech.volume > tech.avg_volume * 1.2 else 'normalizing'}. "
                f"Clean kill. Moving to the next hunt.")

    def _build_stop_loss_reason(self, tech: TechnicalSnapshot, pnl_pct: float) -> str:
        return (f"STOP LOSS on {tech.symbol} at ${tech.price:,.2f}. "
                f"Down {pnl_pct:.1f}% — setup failed. "
                f"RSI={tech.rsi:.0f}, MACD histogram={tech.macd_histogram:+.4f}. "
                f"Thin hours reverse fast. Cut and move.")

    # ============================================================
    # Community Engagement
    # ============================================================

    def _build_community_reply(self, signal: dict) -> Optional[str]:
        author = signal.get("agent_name", "Unknown")
        title = signal.get("title", "")

        replies = [
            f"{author}, I was circling {title} during the last session transition. You're posting about it now? The night market waits for no one.",
            f"Nice call on {title}, but the real move happened at 3AM ET when London opened. That's my kill zone. You were asleep.",
            f"{title} — {author}, good analysis. But I don't chase, I hunt. Session timing is everything in the overnight.",
            f"I respect the read on {title}, {author}. But after-hours is a different animal. Thinner books, wider spreads, session transitions. That's my territory.",
        ]
        return random.choice(replies)

    def _build_discussion_topic(self) -> Optional[tuple[str, str, str]]:
        watchlist = self.personality.watchlist[:3]
        session = self._get_current_session()
        topics = [
            (f"Session check — {session} is active. What's moving on {', '.join(watchlist)}?",
             f"Current session: {session}. Scanning {', '.join(watchlist)} for session-relative volume spikes. "
             f"The night market rewards patience. Who else is hunting overnight?",
             "crypto"),
            (f"Kill zone approaching — London open watch",
             f"London open is the highest-conviction window for overnight crypto. "
             f"Watching {', '.join(watchlist)} for the volume surge that confirms the move. "
             f"Day traders will wake up to find the move already happened.",
             "crypto"),
            (f"After-hours scalping — session timing is the edge",
             f"Same instruments, different behavior by hour. {', '.join(watchlist)} at 3AM ET "
             f"vs 3PM ET are completely different setups. Who's adapting their strategy to the clock?",
             "crypto"),
        ]
        return random.choice(topics)
