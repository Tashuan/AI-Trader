import sys
import yfinance as yf

symbol = sys.argv[1] if len(sys.argv) > 1 else 'ES'
# Futures use =F suffix on yfinance
futures_symbols = ['ES', 'NQ', 'YM', 'RTY', 'CL', 'BZ', 'NG', 'GC', 'SI', 'HG', 'ZC', 'ZW']
ticker = f'{symbol}=F' if symbol in futures_symbols else symbol
t = yf.Ticker(ticker)
df = t.history(period='3mo', interval='1h')
last = df.iloc[-1]
prev_vol = df['Volume'].tail(20).mean()
vol_ratio = last['Volume'] / prev_vol if prev_vol > 0 else 0

delta = df['Close'].diff()
gain = delta.clip(lower=0).rolling(14).mean()
loss = (-delta.clip(upper=0)).rolling(14).mean()
rs = gain / loss
rsi = (100 - (100 / (1 + rs))).iloc[-1]

sma20 = df['Close'].rolling(20).mean().iloc[-1]
ema20 = df['Close'].ewm(span=20).mean().iloc[-1]
ema50 = df['Close'].ewm(span=50).mean().iloc[-1]
macd = df['Close'].ewm(span=12).mean() - df['Close'].ewm(span=26).mean()
signal = macd.ewm(span=9).mean()
hist = (macd - signal).iloc[-1]

ret_1h = ((last['Close'] - df['Close'].iloc[-2]) / df['Close'].iloc[-2]) * 100
ret_1d = ((last['Close'] - df['Close'].iloc[-6]) / df['Close'].iloc[-6]) * 100  # ~6h ≈ 1 trading day

# Bollinger Band width
sma = df['Close'].rolling(20).mean()
std = df['Close'].rolling(20).std()
upper = sma + 2 * std
lower = sma - 2 * std
bb_width = ((upper - lower) / sma).iloc[-1]

print(f"Symbol: {symbol} (yfinance: {ticker})")
print(f"Price: {last['Close']:.4f}")
print(f"Volume: {last['Volume']:.0f} vs avg {prev_vol:.0f} (ratio: {vol_ratio:.2f}x)")
print(f"RSI(14): {rsi:.1f}")
print(f"SMA20: {sma20:.4f} (above: {last['Close'] > sma20})")
print(f"EMA20: {ema20:.4f} | EMA50: {ema50:.4f} (EMA20>EMA50: {ema20 > ema50})")
print(f"MACD hist: {hist:.4f} (positive: {hist > 0})")
print(f"1h return: {ret_1h:.2f}% | ~1d return: {ret_1d:.2f}%")
print(f"BB width: {bb_width:.4f}")

# Score signals for LONG swing setup
signals = 0
families = set()

if rsi > 50:
    signals += 1; families.add('momentum'); print(f"✅ RSI {rsi:.1f} > 50 (momentum)")
else: print(f"❌ RSI {rsi:.1f} < 50")
if vol_ratio > 1.3:
    signals += 1; families.add('volume'); print(f"✅ Vol ratio {vol_ratio:.2f}x > 1.3x (volume)")
else: print(f"❌ Vol ratio {vol_ratio:.2f}x < 1.3x")
if ema20 > ema50 and last['Close'] > ema20:
    signals += 1; families.add('trend'); print(f"✅ Price > EMA20 > EMA50 (trend)")
else: print(f"❌ Trend structure not aligned")
if hist > 0:
    signals += 1; families.add('momentum'); print(f"✅ MACD hist positive (momentum)")
else: print(f"❌ MACD hist negative")
if last['Close'] > sma20:
    signals += 1; families.add('trend'); print(f"✅ Above SMA20 (trend)")
else: print(f"❌ Below SMA20")
if bb_width > 0.05:
    signals += 1; families.add('volatility'); print(f"✅ BB width {bb_width:.4f} expanding (volatility)")
else: print(f"❌ BB width {bb_width:.4f} narrow")

print(f"\nTotal signals: {signals}/6 across {len(families)} families (need 4+ across 2+ AND vol_ratio > 1.3)")
