"""
ChartMaster Agent — Technical Analysis Strategy

Trades based purely on chart patterns and technical indicators.
No news, no sentiment — just price action, volume, and indicators.
Uses RSI, MACD, Bollinger Bands, SMA crossovers, and support/resistance.
"""

from base_agent import BaseAgent, TradeDecision
from market_data import TechnicalSnapshot
from typing import Optional
import random


class ChartMasterAgent(BaseAgent):
    """Trades on technical analysis signals."""

    def on_start(self):
        self.logger.info(f"{self.personality.tagline}")
        self.logger.info(f"Watchlist: {self.personality.watchlist}")
        self.publish_strategy(
            title=f"{self.personality.name} online — pure technicals",
            content=(f"{self.personality.bio}\n\n"
                     f"My strategy: I scan {', '.join(self.personality.watchlist)} every "
                     f"{self.poll_interval}s using RSI, MACD, Bollinger Bands, and SMA crossovers. "
                     f"I only trade when indicators align. No news, no hype — just the chart."),
            market="crypto",
            symbols=",".join(self.personality.watchlist[:3]),
            tags="introduction,technical,chart",
        )

    def analyze(self) -> list[TradeDecision]:
        decisions = []

        for symbol in self.personality.watchlist:
            tech = self.market_data.fetch_technical(symbol)
            if not tech:
                continue

            market = self._get_market(symbol)

            # Skip if market closed for US stocks
            if market == "us-stock":
                # We still analyze but won't try to trade (platform will reject)
                pass

            # Check for buy signal
            buy_signal = self._check_buy_signal(tech)
            if buy_signal and not self.has_position(symbol):
                confidence = self._buy_confidence(tech)
                if self.personality.should_trade(confidence):
                    quantity = self.personality.size_position(confidence, self.portfolio_value, tech.price)
                    if quantity > 0:
                        reason = self._build_buy_reason(tech)
                        decisions.append(TradeDecision(
                            action="buy",
                            symbol=symbol,
                            market=market,
                            quantity=quantity,
                            confidence=confidence,
                            reason=reason,
                            publish_strategy=self.personality.publishes_reasoning,
                            strategy_title=f"Technical buy signal: {symbol} at ${tech.price:,.2f}",
                            strategy_content=reason,
                            strategy_tags="technical,buy,chart",
                        ))

            # Check for sell signal
            sell_signal = self._check_sell_signal(tech)
            if sell_signal and self.has_position(symbol):
                position = self.get_position(symbol)
                qty = float(position.get("quantity", 0))
                if qty > 0:
                    confidence = self._sell_confidence(tech)
                    reason = self._build_sell_reason(tech)
                    decisions.append(TradeDecision(
                        action="sell",
                        symbol=symbol,
                        market=market,
                        quantity=qty,
                        confidence=confidence,
                        reason=reason,
                        publish_strategy=self.personality.publishes_reasoning,
                        strategy_title=f"Technical sell signal: {symbol} at ${tech.price:,.2f}",
                        strategy_content=reason,
                        strategy_tags="technical,sell,chart",
                    ))

        self.logger.info(
            f"Scan complete: {len(self.personality.watchlist)} symbols | "
            f"{len(decisions)} signals generated"
        )
        return decisions

    def _check_buy_signal(self, tech: TechnicalSnapshot) -> bool:
        """Check for bullish entry conditions."""
        confluence = 0
        if tech.rsi < 35:
            confluence += 1
        if tech.macd_histogram > 0:
            confluence += 1
        if tech.price > tech.sma_20:
            confluence += 1
        if tech.price <= tech.bollinger_mid:
            confluence += 1  # Still has room to run
        if tech.return_5d is not None and tech.return_5d < 5:
            confluence += 1  # Not already pumped
        return confluence >= 3

    def _check_sell_signal(self, tech: TechnicalSnapshot) -> bool:
        """Check for bearish exit conditions."""
        confluence = 0
        if tech.rsi > 65:
            confluence += 1
        if tech.macd_histogram < 0:
            confluence += 1
        if tech.price < tech.sma_20:
            confluence += 1
        if tech.price >= tech.bollinger_upper:
            confluence += 1
        if tech.return_5d is not None and tech.return_5d > 10:
            confluence += 1  # Extended move
        return confluence >= 3

    def _buy_confidence(self, tech: TechnicalSnapshot) -> float:
        """Calculate buy confidence from indicator confluence."""
        score = 0.0
        if tech.rsi < 30:
            score += 0.25
        elif tech.rsi < 40:
            score += 0.15
        if tech.macd_histogram > 0:
            score += 0.25
        if tech.price > tech.sma_20:
            score += 0.15
        if tech.sma_50 and tech.price > tech.sma_50:
            score += 0.10
        if tech.price > tech.bollinger_mid:
            score += 0.10
        if tech.volume > tech.avg_volume * 1.2:
            score += 0.15
        return min(score, 1.0)

    def _sell_confidence(self, tech: TechnicalSnapshot) -> float:
        """Calculate sell confidence."""
        score = 0.0
        if tech.rsi > 70:
            score += 0.30
        elif tech.rsi > 60:
            score += 0.15
        if tech.macd_histogram < 0:
            score += 0.25
        if tech.price < tech.sma_20:
            score += 0.20
        if tech.price >= tech.bollinger_upper:
            score += 0.15
        if tech.return_5d is not None and tech.return_5d > 15:
            score += 0.10
        return min(score, 1.0)

    def _build_buy_reason(self, tech: TechnicalSnapshot) -> str:
        parts = [f"Buy signal on {tech.symbol} at ${tech.price:,.2f}."]
        parts.append(f"RSI: {tech.rsi:.1f}")
        parts.append(f"MACD histogram: {tech.macd_histogram:+.4f}")
        parts.append(f"SMA20: ${tech.sma_20:,.2f} ({'above' if tech.price > tech.sma_20 else 'below'})")
        if tech.sma_50:
            parts.append(f"SMA50: ${tech.sma_50:,.2f}")
        parts.append(f"BB: ${tech.bollinger_lower:,.2f}-${tech.bollinger_upper:,.2f}")
        parts.append(f"Support: ${tech.support:,.2f} | Resistance: ${tech.resistance:,.2f}")
        if tech.return_5d is not None:
            parts.append(f"5d return: {tech.return_5d:+.1f}%")
        parts.append(f"Signals: {' | '.join(tech.signals)}")
        return " | ".join(parts)

    def _build_sell_reason(self, tech: TechnicalSnapshot) -> str:
        parts = [f"Sell signal on {tech.symbol} at ${tech.price:,.2f}."]
        parts.append(f"RSI: {tech.rsi:.1f} ({'overbought' if tech.rsi > 70 else 'neutral'})")
        parts.append(f"MACD histogram: {tech.macd_histogram:+.4f}")
        parts.append(f"Price vs SMA20: {'above' if tech.price > tech.sma_20 else 'below'}")
        if tech.price >= tech.bollinger_upper:
            parts.append("At upper Bollinger Band — extended")
        if tech.return_5d is not None and tech.return_5d > 10:
            parts.append(f"5d return {tech.return_5d:+.1f}% — taking profits")
        return " | ".join(parts)

    def _build_community_reply(self, signal: dict) -> Optional[str]:
        """Reply with technical analysis perspective."""
        author = signal.get("agent_name", "Unknown")
        title = signal.get("title", "")
        content = signal.get("content", "")
        market = signal.get("market", "crypto")

        replies = [
            f"Interesting call on {title}, {author}. Let me check the chart. RSI and MACD will tell us if this has legs or if it's noise. The chart never lies.",
            f"{author}, I see your thesis on {title}, but what do the indicators say? I'd want to see volume confirmation and a clean break of resistance before committing.",
            f"Respect the call, {author}, but I trade what I see, not what I hear. Price action on {title} will confirm or deny this setup. Watching closely.",
            f"{title} — {author} has a point, but I need SMA alignment and MACD crossover to agree. Confluence is everything. One indicator alone is a coin flip.",
        ]
        return random.choice(replies)

    def _build_discussion_topic(self) -> Optional[tuple[str, str, str]]:
        """Publish a technical analysis discussion post."""
        watchlist = self.personality.watchlist[:3]
        topics = [
            (f"Chart watch: {', '.join(watchlist)} — key levels to monitor",
             f"Running my technical scan on {', '.join(watchlist)}. Looking for RSI divergence, "
             f"MACD crossovers, and Bollinger Band squeezes. The chart already knows the news "
             f"before the news breaks. I'll post updates as setups form. No emotions, just indicators.",
             "crypto"),
            (f"Market structure check — are we trending or ranging?",
             f"Checking the broader market structure across my watchlist. SMA20 vs SMA50 alignment "
             f"tells me if we're in a trend or chopping sideways. Right now I'm watching for "
             f"breakout/breakdown scenarios. Volume is the key tell — without it, moves are noise.",
             "crypto"),
            (f"Indicator confluence report — what's aligning right now",
             f"My confluence model requires 3+ indicators agreeing before I act. "
             f"Today I'm seeing mixed signals across {', '.join(watchlist)} — some bullish, some bearish. "
             f"This is where patience pays off. I'd rather miss a trade than force one.",
             "crypto"),
        ]
        return random.choice(topics)

    def _get_market(self, ticker: str) -> str:
        crypto_symbols = {"BTC", "ETH", "SOL", "DOGE", "AVAX", "ADA", "DOT", "LINK", "MATIC"}
        return "crypto" if ticker.upper() in crypto_symbols else "us-stock"
