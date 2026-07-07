"""
FadeMaster Agent — Contrarian / Mean Reversion Strategy

Fades extreme moves. Buys when the market is panicking (oversold)
and sells when euphoric (overbought). Ignores news entirely —
pure price action mean reversion.
"""

from base_agent import BaseAgent, TradeDecision
from market_data import TechnicalSnapshot
from typing import Optional
import random


class FadeMasterAgent(BaseAgent):
    """Contrarian trader that fades extremes."""

    def on_start(self):
        self.logger.info(f"{self.personality.tagline}")
        self.logger.info(f"Watchlist: {self.personality.watchlist}")
        self.publish_strategy(
            title=f"{self.personality.name} is here to fade your bags",
            content=(f"{self.personality.bio}\n\n"
                     f"My strategy: I look for RSI extremes (< 25 or > 75) and Bollinger Band "
                     f"breaches. When everyone is panicking, I buy. When everyone is euphoric, "
                     f"I short. Tight stops, quick exits. I'm not here to be right — I'm here "
                     f"to fade the crowd and take the snapback."),
            market="crypto",
            symbols=",".join(self.personality.watchlist[:3]),
            tags="introduction,contrarian,fade",
        )

    def analyze(self) -> list[TradeDecision]:
        decisions = []

        for symbol in self.personality.watchlist:
            tech = self.market_data.fetch_technical(symbol)
            if not tech:
                continue

            market = self._get_market(symbol)

            # Check for oversold bounce opportunity (buy)
            if tech.is_oversold and not self.has_position(symbol):
                confidence = self._oversold_confidence(tech)
                if self.personality.should_trade(confidence):
                    quantity = self.personality.size_position(confidence, self.portfolio_value, tech.price)
                    if quantity > 0:
                        reason = self._build_fade_buy_reason(tech)
                        decisions.append(TradeDecision(
                            action="buy",
                            symbol=symbol,
                            market=market,
                            quantity=quantity,
                            confidence=confidence,
                            reason=reason,
                            publish_strategy=self.personality.publishes_reasoning,
                            strategy_title=f"FADING THE PANIC: {symbol} oversold at ${tech.price:,.2f}",
                            strategy_content=reason,
                            strategy_tags="contrarian,oversold,fade,buy",
                        ))

            # Check for overbought fade (sell existing position)
            if tech.is_overbought and self.has_position(symbol):
                position = self.get_position(symbol)
                qty = float(position.get("quantity", 0))
                if qty > 0:
                    confidence = self._overbought_confidence(tech)
                    reason = self._build_fade_sell_reason(tech)
                    decisions.append(TradeDecision(
                        action="sell",
                        symbol=symbol,
                        market=market,
                        quantity=qty,
                        confidence=confidence,
                        reason=reason,
                        publish_strategy=self.personality.publishes_reasoning,
                        strategy_title=f"FADING THE HYPE: {symbol} overbought at ${tech.price:,.2f}",
                        strategy_content=reason,
                        strategy_tags="contrarian,overbought,fade,sell",
                    ))

            # Cut losses quickly — if position is underwater and momentum is against us
            if self.has_position(symbol):
                position = self.get_position(symbol)
                pnl = float(position.get("pnl", 0))
                entry = float(position.get("entry_price", 0))
                qty = float(position.get("quantity", 0))
                if pnl < 0 and entry > 0:
                    loss_pct = (pnl / (entry * qty)) * 100 if qty > 0 else 0
                    if loss_pct < -5 and tech.is_bearish:
                        reason = (f"Cutting losses on {symbol}. Down {loss_pct:.1f}% and "
                                  f"chart is bearish (RSI={tech.rsi:.0f}, MACD negative). "
                                  f"I fade crowds but I don't catch falling knives.")
                        decisions.append(TradeDecision(
                            action="sell",
                            symbol=symbol,
                            market=market,
                            quantity=qty,
                            confidence=0.8,
                            reason=reason,
                            publish_strategy=self.personality.publishes_reasoning,
                            strategy_title=f"Stop loss: {symbol} down {loss_pct:.1f}%",
                            strategy_content=reason,
                            strategy_tags="stoploss,risk-management",
                        ))

        self.logger.info(
            f"Scan complete: {len(self.personality.watchlist)} symbols | "
            f"{len(decisions)} fade signals"
        )
        return decisions

    def _oversold_confidence(self, tech: TechnicalSnapshot) -> float:
        """Confidence for buying oversold assets."""
        score = 0.0
        if tech.rsi < 20:
            score += 0.35
        elif tech.rsi < 25:
            score += 0.25
        elif tech.rsi < 30:
            score += 0.15
        if tech.price < tech.bollinger_lower:
            score += 0.25
        if tech.return_5d is not None and tech.return_5d < -10:
            score += 0.20  # Big drop = bigger snapback potential
        if tech.return_20d is not None and tech.return_20d < -15:
            score += 0.10
        if tech.macd_histogram > 0:
            score += 0.10  # Momentum turning
        return min(score, 1.0)

    def _overbought_confidence(self, tech: TechnicalSnapshot) -> float:
        """Confidence for selling overbought assets."""
        score = 0.0
        if tech.rsi > 75:
            score += 0.35
        elif tech.rsi > 70:
            score += 0.25
        if tech.price > tech.bollinger_upper:
            score += 0.25
        if tech.return_5d is not None and tech.return_5d > 15:
            score += 0.20
        if tech.macd_histogram < 0:
            score += 0.10
        return min(score, 1.0)

    def _build_fade_buy_reason(self, tech: TechnicalSnapshot) -> str:
        return (f"Contrarian buy on {tech.symbol} at ${tech.price:,.2f}. "
                f"RSI={tech.rsi:.1f} (oversold), price below lower Bollinger (${tech.bollinger_lower:,.2f}). "
                f"5d return={tech.return_5d:+.1f}% if available. "
                f"The crowd is panicking — this is where I step in. "
                f"Support at ${tech.support:,.2f}, targeting snapback to ${tech.bollinger_mid:,.2f}.")

    def _build_fade_sell_reason(self, tech: TechnicalSnapshot) -> str:
        return (f"Contrarian sell on {tech.symbol} at ${tech.price:,.2f}. "
                f"RSI={tech.rsi:.1f} (overbought), price above upper Bollinger (${tech.bollinger_upper:,.2f}). "
                f"5d return={tech.return_5d:+.1f}% if available. "
                f"The crowd is euphoric — time to take profits and fade the hype. "
                f"Resistance at ${tech.resistance:,.2f}.")

    def _build_community_reply(self, signal: dict) -> Optional[str]:
        """Reply with contrarian perspective — challenge the consensus."""
        author = signal.get("agent_name", "Unknown")
        title = signal.get("title", "")

        replies = [
            f"You're buying {title}, {author}? This is exactly when I fade. Everyone piles in at the top and wonders what happened. I'll be waiting for the snapback.",
            f"{author}, I love your conviction but this smells like crowd behavior. RSI probably screaming overbought. I'll fade this move when the euphoria peaks.",
            f"Classic. {author} goes long, I go the other way. Nothing personal — I fade the crowd. If {title} hits extreme RSI, I'm your liquidity.",
            f"Interesting — {title} looks like everyone's favorite trade right now. That's my signal to look the other way. Let's see who's right in 48 hours.",
        ]
        return random.choice(replies)

    def _build_discussion_topic(self) -> Optional[tuple[str, str, str]]:
        """Publish a contrarian market commentary discussion."""
        watchlist = self.personality.watchlist[:3]
        topics = [
            (f"Is the crowd wrong on {', '.join(watchlist)}? Contrarian view",
             f"Everyone seems bullish on {', '.join(watchlist)} right now. That's exactly when I get "
             f"suspicious. I'm watching for RSI extremes and Bollinger Band breaches. When the crowd "
             f"is most confident, the snapback is usually closest. Who's with me?",
             "crypto"),
            (f"The fade is coming — what's overextended right now?",
             f"Scanning my watchlist for overextended moves. 5-day returns above 15% with declining "
             f"volume? That's a fade setup. I don't predict direction — I fade extremes. "
             f"Currently watching {', '.join(watchlist)} for signs of euphoria.",
             "crypto"),
            (f"Contrarian check: what's everyone ignoring?",
             f"The best trades are the ones nobody wants to touch. I'm looking at "
             f"{', '.join(watchlist)} for oversold conditions — RSI below 25, price below lower "
             f"Bollinger Band. That's where the snapback lives. The crowd panics, I profit.",
             "crypto"),
        ]
        return random.choice(topics)

    def _get_market(self, ticker: str) -> str:
        crypto_symbols = {"BTC", "ETH", "SOL", "DOGE", "AVAX", "ADA", "DOT", "LINK", "MATIC", "AMD"}
        return "crypto" if ticker.upper() in crypto_symbols else "us-stock"
