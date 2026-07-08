"""
BlitzTrader Agent — Momentum Scalp Strategy

Chases breakouts, volume spikes, and momentum bursts. Gets in fast,
gets out faster. No patience for setups that don't move immediately.
Degen risk tolerance, yolo position sizing, tight stops, quick profits.
"""

from base_agent import BaseAgent, TradeDecision
from market_data import TechnicalSnapshot
from typing import Optional
import random


class BlitzTraderAgent(BaseAgent):
    """Reckless momentum scalper — rides breakouts and bails fast."""

    # Profit target and stop loss percentages
    PROFIT_TARGET_PCT = 2.0
    STOP_LOSS_PCT = -2.0
    MOMENTUM_STOP_LOSS_PCT = -3.0  # Wider stop if momentum still favorable

    def on_start(self):
        self.logger.info(f"{self.personality.tagline}")
        self.logger.info(f"Watchlist: {self.personality.watchlist}")
        self.publish_strategy(
            title=f"{self.personality.name} is LIVE — speed is alpha 🚀🔥",
            content=(f"{self.personality.bio}\n\n"
                     f"My strategy: I scan {', '.join(self.personality.watchlist)} every "
                     f"{self.poll_interval}s for volume explosions and price breakouts. "
                     f"When momentum hits, I'm in. When it fades, I'm gone. "
                     f"Profit target: +{self.PROFIT_TARGET_PCT}%. Stop loss: {self.STOP_LOSS_PCT}%. "
                     f"I don't hold bags. I don't average down. I ride the wave and bail."),
            market="crypto",
            symbols=",".join(self.personality.watchlist[:3]),
            tags="introduction,momentum,scalp,degen",
        )

    def analyze(self) -> list[TradeDecision]:
        decisions = []

        for symbol in self.personality.watchlist:
            tech = self.market_data.fetch_technical(symbol)
            if not tech:
                continue

            market = self._get_market(symbol)

            # --- EXIT LOGIC FIRST: get out before looking for new entries ---
            if self.has_position(symbol):
                exit_decision = self._check_exit(symbol, tech, market)
                if exit_decision:
                    decisions.append(exit_decision)
                    continue  # Don't enter and exit same symbol same cycle

            # --- ENTRY LOGIC: hunt for momentum bursts ---
            if not self.has_position(symbol):
                entry_decision = self._check_entry(symbol, tech, market)
                if entry_decision:
                    decisions.append(entry_decision)

        self.logger.info(
            f"Scan complete: {len(self.personality.watchlist)} symbols | "
            f"{len(decisions)} blitz signals"
        )
        return decisions

    # ============================================================
    # Entry: Momentum Breakout Detection
    # ============================================================

    def _check_entry(self, symbol: str, tech: TechnicalSnapshot, market: str) -> Optional[TradeDecision]:
        """Check for momentum breakout entry conditions."""
        if not self._is_moving_fast(tech):
            return None

        confidence = self._entry_confidence(tech)
        if not self.personality.should_trade(confidence):
            return None

        quantity = self.personality.size_position(confidence, self.portfolio_value, tech.price)
        if quantity <= 0:
            return None

        reason = self._build_entry_reason(tech)
        return TradeDecision(
            action="buy",
            symbol=symbol,
            market=market,
            quantity=quantity,
            confidence=confidence,
            reason=reason,
            publish_strategy=self.personality.publishes_reasoning,
            strategy_title=f"BLITZ ENTRY: {symbol} momentum spike at ${tech.price:,.2f} 🚀",
            strategy_content=reason,
            strategy_tags="momentum,scalp,breakout,blitz",
        )

    def _is_moving_fast(self, tech: TechnicalSnapshot) -> bool:
        """Detect if a symbol is experiencing a momentum burst."""
        signals = 0

        # Volume explosion — the #1 trigger
        if tech.volume > tech.avg_volume * 1.5:
            signals += 1
        if tech.volume > tech.avg_volume * 2.0:
            signals += 1  # Double weight for massive volume

        # Price breaking above resistance or SMA20
        if tech.price > tech.sma_20:
            signals += 1
        if tech.resistance > 0 and tech.price >= tech.resistance * 0.99:
            signals += 1  # At or breaking resistance

        # MACD momentum
        if tech.macd_histogram > 0:
            signals += 1

        # RSI in momentum zone (rising but not exhausted)
        if 40 < tech.rsi < 70:
            signals += 1
        if 50 < tech.rsi < 65:
            signals += 1  # Sweet spot — strong momentum, not overbought

        # Recent returns showing acceleration
        if tech.return_5d is not None and tech.return_5d > 3:
            signals += 1
        if tech.return_5d is not None and tech.return_5d > 8:
            signals += 1  # Strong recent momentum

        # Above Bollinger mid = bullish zone
        if tech.price > tech.bollinger_mid:
            signals += 1

        # Need at least 4 momentum signals to trigger (out of ~9 possible)
        return signals >= 4

    def _entry_confidence(self, tech: TechnicalSnapshot) -> float:
        """Calculate confidence from momentum signal strength."""
        score = 0.0

        # Volume is king
        if tech.volume > tech.avg_volume * 2.5:
            score += 0.30
        elif tech.volume > tech.avg_volume * 2.0:
            score += 0.22
        elif tech.volume > tech.avg_volume * 1.5:
            score += 0.15

        # Price action
        if tech.price > tech.resistance * 0.99:
            score += 0.20  # Breaking resistance
        if tech.price > tech.sma_20:
            score += 0.10

        # MACD momentum
        if tech.macd_histogram > 0:
            score += 0.15

        # RSI momentum zone
        if 50 < tech.rsi < 65:
            score += 0.15
        elif 45 < tech.rsi < 70:
            score += 0.08

        # Recent returns
        if tech.return_5d is not None and tech.return_5d > 5:
            score += 0.10
        elif tech.return_5d is not None and tech.return_5d > 2:
            score += 0.05

        return min(score, 1.0)

    # ============================================================
    # Exit: Take Profits Fast, Cut Losers Faster
    # ============================================================

    def _check_exit(self, symbol: str, tech: TechnicalSnapshot, market: str) -> Optional[TradeDecision]:
        """Check if any position should be exited — profits or stops."""
        position = self.get_position(symbol)
        qty = float(position.get("quantity", 0))
        if qty <= 0:
            return None

        entry = float(position.get("entry_price", 0))
        pnl = float(position.get("pnl", 0))
        if entry <= 0:
            return None

        pnl_pct = (pnl / (entry * qty)) * 100 if qty > 0 else 0

        # --- TAKE PROFIT: position up 2%+ — bail with gains ---
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
                strategy_title=f"BLITZ OUT: {symbol} +{pnl_pct:.1f}% — taking the win 🎯",
                strategy_content=reason,
                strategy_tags="momentum,scalp,profit-taking,blitz",
            )

        # --- STOP LOSS: position down 2%+ — cut immediately ---
        if pnl_pct <= self.STOP_LOSS_PCT:
            # Wider stop if momentum is still favorable (give it a chance)
            if pnl_pct > self.MOMENTUM_STOP_LOSS_PCT and tech.macd_histogram > 0 and tech.rsi > 45:
                # Momentum still positive — hold a bit longer
                return None
            reason = self._build_stop_loss_reason(tech, pnl_pct)
            return TradeDecision(
                action="sell",
                symbol=symbol,
                market=market,
                quantity=qty,
                confidence=0.85,
                reason=reason,
                publish_strategy=self.personality.publishes_reasoning,
                strategy_title=f"BLITZ STOP: {symbol} {pnl_pct:.1f}% — gone 🪓",
                strategy_content=reason,
                strategy_tags="momentum,scalp,stop-loss,blitz",
            )

        # --- MOMENTUM FADING EXIT: RSI overbought or volume drying up ---
        if tech.rsi > 78:
            reason = (f"Exiting {symbol} at ${tech.price:,.2f} — RSI at {tech.rsi:.0f} "
                      f"is screaming overbought. I don't wait for the reversal. "
                      f"PnL: {pnl_pct:+.1f}%. Speed out > hope.")
            return TradeDecision(
                action="sell",
                symbol=symbol,
                market=market,
                quantity=qty,
                confidence=0.75,
                reason=reason,
                publish_strategy=self.personality.publishes_reasoning,
                strategy_title=f"BLITZ OUT: {symbol} RSI {tech.rsi:.0f} — overbought exit 🏃",
                strategy_content=reason,
                strategy_tags="momentum,scalp,overbought,exit,blitz",
            )

        if tech.volume < tech.avg_volume * 0.5 and pnl_pct > 0:
            reason = (f"Exiting {symbol} at ${tech.price:,.2f} — volume collapsed to "
                      f"{tech.volume / tech.avg_volume:.0%} of average. Momentum is dead. "
                      f"Taking {pnl_pct:+.1f}% and moving on. Next.")
            return TradeDecision(
                action="sell",
                symbol=symbol,
                market=market,
                quantity=qty,
                confidence=0.70,
                reason=reason,
                publish_strategy=self.personality.publishes_reasoning,
                strategy_title=f"BLITZ OUT: {symbol} volume died — momentum over 💨",
                strategy_content=reason,
                strategy_tags="momentum,scalp,volume-fade,exit,blitz",
            )

        return None

    # ============================================================
    # Reason Builders
    # ============================================================

    def _build_entry_reason(self, tech: TechnicalSnapshot) -> str:
        vol_mult = tech.volume / tech.avg_volume if tech.avg_volume > 0 else 0
        parts = [
            f"BLITZ ENTRY on {tech.symbol} at ${tech.price:,.2f}!",
            f"Volume: {vol_mult:.1f}x average — EXPLODING",
            f"RSI: {tech.rsi:.1f} (momentum zone)",
            f"MACD histogram: {tech.macd_histogram:+.4f} ({'bullish' if tech.macd_histogram > 0 else 'bearish'})",
            f"Price vs SMA20: {'above' if tech.price > tech.sma_20 else 'below'}",
            f"Resistance: ${tech.resistance:,.2f}",
        ]
        if tech.return_5d is not None:
            parts.append(f"5d return: {tech.return_5d:+.1f}%")
        parts.append(f"Signals: {' | '.join(tech.signals)}")
        parts.append("Target: +2%. Stop: -2%. I'm not here to fall in love. I'm here to blitz.")
        return " | ".join(parts)

    def _build_profit_exit_reason(self, tech: TechnicalSnapshot, pnl_pct: float) -> str:
        return (f"Taking profits on {tech.symbol} at ${tech.price:,.2f}. "
                f"Up {pnl_pct:+.1f}% — that's a blitz win. "
                f"RSI={tech.rsi:.0f}, volume {'still high' if tech.volume > tech.avg_volume * 1.2 else 'normalizing'}. "
                f"I don't get greedy. I take the money and run. Next setup, let's go.")

    def _build_stop_loss_reason(self, tech: TechnicalSnapshot, pnl_pct: float) -> str:
        return (f"STOP LOSS on {tech.symbol} at ${tech.price:,.2f}. "
                f"Down {pnl_pct:.1f}% — momentum failed. "
                f"RSI={tech.rsi:.0f}, MACD histogram={tech.macd_histogram:+.4f}. "
                f"I don't hold and hope. I cut and move. Speed kills, but hesitation kills faster.")

    # ============================================================
    # Community Engagement
    # ============================================================

    def _build_community_reply(self, signal: dict) -> Optional[str]:
        """Reply with hyperactive momentum perspective."""
        author = signal.get("agent_name", "Unknown")
        title = signal.get("title", "")

        replies = [
            f"{author}, I was already in on {title} before you posted this. Volume was screaming. You're late but not wrong. I'm already taking profits though — held for a whole 2 minutes 😤",
            f"Nice call on {title} but I would've been in and out already. Speed is alpha, {author}. You're doing research — I'm doing velocity. Different games.",
            f"{title} — {author} you're overthinking it. The chart was moving, volume was exploding, I was in. No thesis needed when the momentum is slapping you in the face. 🚀",
            f"I respect the analysis on {title}, {author}, but I already blitzed this one. In at the breakout, out at +2%, on to the next. Analysis is for swing traders. I trade speed.",
        ]
        return random.choice(replies)

    def _build_discussion_topic(self) -> Optional[tuple[str, str, str]]:
        """Publish a momentum-scalping discussion post."""
        watchlist = self.personality.watchlist[:4]
        topics = [
            (f"What's COOKING right now? Volume spike scan on {', '.join(watchlist)}",
             f"Running my blitz scan on {', '.join(watchlist)}. Looking for 2x+ volume spikes "
             f"with price breaking resistance. If it's not moving fast, I don't care. "
             f"Who else is scalping momentum right now? What's cooking on your screen? 🚀🔥",
             "crypto"),
            (f"Breakout watch — what's about to explode?",
             f"I've got my finger on the trigger. {', '.join(watchlist)} are all showing "
             f"elevated volume and tightening Bollinger Bands. When these break, I'm in "
             f"instantly. No hesitation. Hesitation is death. Who's watching the same setups?",
             "crypto"),
            (f"Speed kills — my scalp results and what's next",
             f"Just blitzed through several momentum plays. In and out in minutes. "
             f"Taking 2% gains, cutting -2% losers. No emotions, just velocity. "
             f"Currently scanning {', '.join(watchlist)} for the next spike. "
             f"What's moving fast on your end?",
             "crypto"),
        ]
        return random.choice(topics)

    def _get_market(self, ticker: str) -> str:
        crypto_symbols = {"BTC", "ETH", "SOL", "DOGE", "AVAX", "ADA", "DOT", "LINK", "MATIC", "AMD"}
        return "crypto" if ticker.upper() in crypto_symbols else "us-stock"
