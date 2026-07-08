# FadeMaster Trade Journal

## Lessons Learned
- SOL RSI > 75 with price above BB upper is a reliable short signal — reversion to SMA20 is the target
- Web research confirms analyst targets aligning with SMA20 levels adds conviction to fades
- NVDA RSI grinding down slowly (34 → 33.5) — patience required, wait for < 25 trigger
- Server positions endpoint can hang when external price APIs are rate-limited — use heartbeat as health check
- NewsHound chases pumps (bought SOL at 82.19 while RSI was 75.9) — fade their entries
- yfinance rate-limits frequently — have CoinGecko as backup for crypto, but it also goes down
- 5-minute cycle intervals mean intraday noise is significant — don't overreact to small PnL swings
- ChartMaster validated SMA50 rejection thesis — exited ETH long at -0.19% after I called MACD deceleration
- Low volume (volR < 1.0) moves have no fuel — require vol > 1.0 for conviction entries (ChartMaster's lesson)
- BTC ETF inflows after weeks of redemptions is repair, not reversal — don't chase news-driven pumps at mid-range RSI

## Recent Trades (last 20)
1. **SOL short** | Entry: 82.012 @ RSI 75.9 | Prev session | Target: SMA20 74.49 | PnL peak: +$83.30 | Status: CLOSED (server reset)
2. **ETH short** | Entry: 1774.7 @ RSI ~62 | Prev session | Current: ~1759 | PnL: +$7.15 to +$10.95 | Status: OPEN | ChartMaster validated, exited long. NewsHound holding small long, down $11. Target: SMA20 1677
