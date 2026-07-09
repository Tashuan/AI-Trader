import { motion } from 'framer-motion';
import { useState, useEffect } from 'react';
import type { MarketData } from '../types';

interface MarketChipProps {
  symbol: string;
  data: MarketData;
}

export function MarketChip({ symbol, data }: MarketChipProps) {
  const watching = data.agents_watching || 0;
  const bullish = data.bullish_count || 0;
  const bearish = data.bearish_count || 0;
  const total = bullish + bearish;
  const bullPct = total > 0 ? (bullish / total) * 100 : 50;

  const heatColor = watching >= 5 ? 'glow-orange' : watching >= 3 ? 'glow-blue' : '';

  return (
    <div className={`card-base px-3 py-2 flex items-center gap-3 min-w-[140px] ${heatColor}`}>
      <div className="flex flex-col">
        <span className="text-xs font-semibold text-white">{symbol}</span>
        {data.price > 0 && (
          <span className="text-[10px] font-mono text-arena-text-secondary">
            ${data.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}
          </span>
        )}
      </div>

      <div className="flex flex-col items-end ml-auto">
        <div className="flex items-center gap-1">
          <span className="text-[10px] text-arena-green">▲{bullish}</span>
          <span className="text-[10px] text-arena-red">▼{bearish}</span>
        </div>
        {watching > 0 && (
          <span className="text-[9px] text-arena-text-dim">{watching} agents</span>
        )}
      </div>

      {/* Bull/bear split bar */}
      <div className="absolute bottom-0 left-0 right-0 h-0.5 rounded-b-xl overflow-hidden flex">
        <div className="bg-arena-green" style={{ width: `${bullPct}%` }} />
        <div className="bg-arena-red" style={{ width: `${100 - bullPct}%` }} />
      </div>
    </div>
  );
}

interface TopMarketBarProps {
  markets: Record<string, MarketData>;
  breakingEvent: { headline: string; source: string; timestamp: string } | null;
}

export function TopMarketBar({ markets, breakingEvent }: TopMarketBarProps) {
  const symbols = Object.keys(markets);

  return (
    <div className="relative h-[90px] border-b border-arena-border bg-arena-card/50 backdrop-blur-sm flex items-center px-4 gap-3 overflow-hidden">
      {/* Logo + LIVE indicator */}
      <div className="flex items-center gap-3 shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold tracking-tight text-white">AI TRADER</span>
          <span className="text-sm font-bold text-arena-purple">ARENA</span>
        </div>
        <div className="flex items-center gap-1.5">
          <motion.span
            className="w-2 h-2 rounded-full bg-arena-red"
            animate={{ opacity: [1, 0.3, 1] }}
            transition={{ duration: 1.5, repeat: Infinity }}
          />
          <span className="text-[10px] font-mono text-arena-red">LIVE</span>
        </div>
      </div>

      {/* Divider */}
      <div className="h-8 w-px bg-arena-border shrink-0" />

      {/* Market chips */}
      <div className="flex items-center gap-2 overflow-x-auto flex-1">
        {symbols.map(sym => (
          <div key={sym} className="relative">
            <MarketChip symbol={sym} data={markets[sym]} />
          </div>
        ))}
      </div>

      {/* Clock */}
      <div className="shrink-0 text-right">
        <Clock />
      </div>

      {/* Breaking event banner */}
      {breakingEvent && (
        <motion.div
          className="absolute top-0 left-0 right-0 h-full bg-arena-red/20 backdrop-blur-sm flex items-center justify-center px-4 z-10"
          initial={{ y: -90 }}
          animate={{ y: 0 }}
          exit={{ y: -90 }}
          transition={{ duration: 0.4 }}
        >
          <span className="text-xs font-semibold text-arena-red mr-2">BREAKING:</span>
          <span className="text-sm text-white truncate">{breakingEvent.headline}</span>
        </motion.div>
      )}
    </div>
  );
}

function Clock() {
  const [time, setTime] = useState(new Date());

  useEffect(() => {
    const interval = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="flex flex-col items-end">
      <span className="text-xs font-mono text-arena-text-secondary">
        {time.toLocaleTimeString('en-US', { hour12: false })}
      </span>
      <span className="text-[9px] text-arena-text-dim">UTC</span>
    </div>
  );
}

