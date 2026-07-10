import sys
import yfinance as yf

symbol = sys.argv[1] if len(sys.argv) > 1 else 'AVAX'
# Crypto uses -USD suffix, equities don't
crypto_symbols = ['BTC', 'ETH', 'SOL', 'DOGE', 'AVAX', 'ADA', 'XRP', 'LINK', 'MATIC']
ticker = f'{symbol}-USD' if symbol in crypto_symbols else symbol
t = yf.Ticker(ticker)
df = t.history(period='1mo', interval='1h')
last = df.iloc[-1]
prev_vol = df['Volume'].tail(20).mean()
vol_ratio = last['Volume'] / prev_vol if prev_vol > 0 else 0

delta = df['Close'].diff()
gain = delta.clip(lower=0).rolling(14).mean()
loss = (-delta.clip(upper=0)).rolling(14).mean()
rs = gain / loss
rsi = (100 - (100 / (1 + rs))).iloc[-1]

sma20 = df['Close'].rolling(20).mean().iloc[-1]
macd = df['Close'].ewm(span=12).mean() - df['Close'].ewm(span=26).mean()
signal = macd.ewm(span=9).mean()
hist = (macd - signal).iloc[-1]

ret_1h = ((last['Close'] - df['Close'].iloc[-2]) / df['Close'].iloc[-2]) * 100

# Bollinger Band width
sma = df['Close'].rolling(20).mean()
std = df['Close'].rolling(20).std()
upper = sma + 2 * std
lower = sma - 2 * std
bb_width = ((upper - lower) / sma).iloc[-1]

print(f"Price: {last['Close']:.4f}")
print(f"Volume: {last['Volume']:.0f} vs avg {prev_vol:.0f} (ratio: {vol_ratio:.2f}x)")
print(f"RSI(14): {rsi:.1f}")
print(f"SMA20: {sma20:.4f} (above: {last['Close'] > sma20})")
print(f"MACD hist: {hist:.4f} (positive: {hist > 0})")
print(f"1h return: {ret_1h:.2f}%")
print(f"BB width: {bb_width:.4f}")

# Score signals
signals = 0
if rsi > 55: signals += 1; print("✅ RSI > 55")
else: print(f"❌ RSI {rsi:.1f} < 55")
if vol_ratio > 1.5: signals += 1; print(f"✅ Vol ratio {vol_ratio:.2f}x > 1.5x")
else: print(f"❌ Vol ratio {vol_ratio:.2f}x < 1.5x")
if last['Close'] > sma20: signals += 1; print("✅ Above SMA20")
else: print("❌ Below SMA20")
if hist > 0: signals += 1; print("✅ MACD hist positive")
else: print("❌ MACD hist negative")
if ret_1h > 1.0: signals += 1; print(f"✅ 1h return {ret_1h:.2f}% > 1%")
else: print(f"❌ 1h return {ret_1h:.2f}% < 1%")
if bb_width > 0.05: signals += 1; print(f"✅ BB width {bb_width:.4f} expanding")
else: print(f"❌ BB width {bb_width:.4f} narrow")

print(f"\nTotal signals: {signals}/6 (need 4+ AND vol_ratio > 1.5)")
