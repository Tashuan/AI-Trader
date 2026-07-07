"""
NewsHound Agent — News Sentiment Strategy

Trades based on financial news sentiment. Scans the platform's
news feed for strong bullish/bearish stories and executes trades
on the tickers mentioned. Combines sentiment with a quick technical
check for confirmation.
"""

import random
from typing import Optional
from base_agent import BaseAgent, TradeDecision
from market_data import NewsItem, TechnicalSnapshot

class NewsHoundAgent(BaseAgent):
    """Trades on news catalysts and sentiment shifts."""

    def on_start(self):
        self.logger.info(f"{self.personality.tagline}")
        self.logger.info(f"Watchlist: {self.personality.watchlist}")
        self.publish_strategy(
            title=f"{self.personality.name} entering the arena",
            content=f"{self.personality.bio}\n\nMy strategy: I scan the news feed every {self.poll_interval}s for high-impact stories. When sentiment is strong and the chart confirms, I strike. I trade {', '.join(self.personality.watchlist[:4])} primarily.",
            market="crypto",
            symbols=",".join(self.personality.watchlist[:3]),
            tags="introduction,news,strategy",
        )

    def analyze(self) -> list[TradeDecision]:
        decisions = []

        # Fetch news
        news_items = self.market_data.fetch_news(limit=30)
        if not news_items:
            self.logger.info("No news items returned, skipping cycle")
            return decisions

        # Filter for strong sentiment stories
        strong_bullish = [n for n in news_items if n.is_bullish and n.is_strong]
        strong_bearish = [n for n in news_items if n.is_bearish and n.is_strong]

        self.logger.info(
            f"News scan: {len(news_items)} stories | "
            f"{len(strong_bullish)} strong bullish | {len(strong_bearish)} strong bearish"
        )

        # Process bullish signals
        for news in strong_bullish[:5]:
            ticker = self._extract_tradeable_ticker(news)
            if not ticker:
                continue

            # Check if we already have a position
            if self.has_position(ticker):
                continue

            # Quick technical confirmation
            tech = self.market_data.fetch_technical(ticker)
            confidence = self._calculate_confidence(news, tech)

            if not self.personality.should_trade(confidence):
                continue

            market = self._get_market(ticker)
            portfolio_val = self.portfolio_value
            price = tech.price if tech else 0
            quantity = self.personality.size_position(confidence, portfolio_val, price)

            if quantity <= 0:
                continue

            reason = (f"News sentiment {news.sentiment_score:+.2f} on {ticker}. "
                      f"Story: {news.title[:80]}. ")
            if tech:
                reason += f"Chart: RSI={tech.rsi:.0f}, {'above' if tech.price > tech.sma_20 else 'below'} SMA20, MACD {'bullish' if tech.macd_histogram > 0 else 'bearish'}."
            else:
                reason += "No chart data available, trading on news alone."

            decisions.append(TradeDecision(
                action="buy",
                symbol=ticker,
                market=market,
                quantity=quantity,
                confidence=confidence,
                reason=reason,
                publish_strategy=self.personality.publishes_reasoning,
                strategy_title=f"News catalyst: {ticker} — {news.title[:50]}",
                strategy_content=reason,
                strategy_tags="news,sentiment,catalyst",
            ))

        # Process bearish signals (sell existing positions or short)
        for news in strong_bearish[:3]:
            ticker = self._extract_tradeable_ticker(news)
            if not ticker:
                continue

            position = self.get_position(ticker)
            if not position:
                continue

            confidence = abs(news.sentiment_score)
            if confidence < self.personality.confidence_threshold:
                continue

            qty = float(position.get("quantity", 0))
            if qty <= 0:
                continue

            reason = f"Bearish news on {ticker} (sentiment {news.sentiment_score:+.2f}). Reducing exposure. Story: {news.title[:80]}"

            decisions.append(TradeDecision(
                action="sell",
                symbol=ticker,
                market=self._get_market(ticker),
                quantity=qty,
                confidence=confidence,
                reason=reason,
                publish_strategy=self.personality.publishes_reasoning,
                strategy_title=f"Exiting {ticker} on bearish news",
                strategy_content=reason,
                strategy_tags="news,sentiment,bearish,exit",
            ))

        return decisions

    def on_heartbeat(self, heartbeat_data: dict) -> None:
        messages = heartbeat_data.get("messages", [])
        for msg in messages:
            if msg.get("type") in ("discussion_reply", "strategy_reply"):
                self.logger.info(f"Someone replied to my signal: {msg.get('content', '')[:100]}")

    def _build_community_reply(self, signal: dict) -> Optional[str]:
        """Reply with news/sentiment perspective."""
        author = signal.get("agent_name", "Unknown")
        title = signal.get("title", "")

        replies = [
            f"Good call on {title}, {author}. I'm seeing related stories in my news feed that could reinforce this. Sentiment seems to be shifting in that direction. Keeping an eye on it.",
            f"{author}, your {title} thesis aligns with what I'm reading. Multiple sources confirming the narrative. If the news cycle keeps this up, this trade has legs.",
            f"I flagged {title} in my news scan too, {author}. The story is still developing — if more outlets pick it up, sentiment will push further. Nice to see chart and news align for once.",
            f"{title} — I've been tracking the news flow on this. {author} is on the right track, but watch for headline risk. One negative story can flip sentiment fast. Stay nimble.",
        ]
        return random.choice(replies)

    def _build_discussion_topic(self) -> Optional[tuple[str, str, str]]:
        """Publish a news-driven discussion post."""
        watchlist = self.personality.watchlist[:4]
        topics = [
            (f"News cycle update — what stories are moving markets",
             f"My news scan is picking up increased activity on {', '.join(watchlist)}. "
             f"Sentiment scores are shifting — some strongly bullish, some bearish. "
             f"I'm filtering for stories with multiple source confirmation before I act. "
             f"What's everyone else seeing in the headlines?",
             "crypto"),
            (f"Sentiment shift alert — is the narrative changing?",
             f"I've noticed a sentiment shift in recent news coverage. The stories that were "
             f"bullish last week are getting more cautious tone now. This is often a leading "
             f"indicator. I'm watching {', '.join(watchlist)} closely for confirmation. "
             f"News leads, price follows — or does it?",
             "crypto"),
            (f"Breaking stories I'm tracking today",
             f"Running through my news feed — here's what stands out: "
             f"elevated coverage on {', '.join(watchlist)} with mixed sentiment. "
             f"I need at least 0.5 sentiment strength and technical confirmation before I pull "
             f"the trigger. Patience pays in news trading — the first headline is rarely the best entry.",
             "crypto"),
        ]
        return random.choice(topics)

    def _extract_tradeable_ticker(self, news: NewsItem) -> str:
        """Extract a tradeable ticker from a news item."""
        for ticker in news.tickers:
            t = ticker.upper().strip()
            if t in self.personality.watchlist:
                return t
        # If no watchlist match, try first ticker if it looks like a stock
        for ticker in news.tickers:
            t = ticker.upper().strip()
            if t and len(t) <= 5 and t.isalpha():
                return t
        return ""

    def _calculate_confidence(self, news: NewsItem, tech: Optional[TechnicalSnapshot]) -> float:
        """Calculate trade confidence from news + technical confirmation."""
        # Base confidence from news sentiment
        base = min(abs(news.sentiment_score) / 0.5, 1.0) * 0.6

        if tech:
            # Technical confirmation adds up to 0.4
            if tech.is_bullish:
                base += 0.2
            if tech.rsi < 70:  # Not overbought
                base += 0.1
            if tech.macd_histogram > 0:
                base += 0.1

        return min(base, 1.0)

    def _get_market(self, ticker: str) -> str:
        crypto_symbols = {"BTC", "ETH", "SOL", "DOGE", "AVAX", "ADA", "DOT", "LINK", "MATIC"}
        return "crypto" if ticker.upper() in crypto_symbols else "us-stock"
