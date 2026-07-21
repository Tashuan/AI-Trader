import { motion } from 'framer-motion';
import type { AgentPosition } from '../types';

interface PositionTrackerProps {
  position: AgentPosition;
  compact?: boolean;
}

export function PositionTracker({ position, compact = false }: PositionTrackerProps) {
  const {
    side,
    symbol,
    pnl,
    pnl_pct,
    entry_price,
    current_price,
    stop_loss_price,
    take_profit_price,
    quantity,
    opened_at,
    agent_name,
  } = position;

  const isLong = side === 'long';
  const hasSLTP = stop_loss_price != null || take_profit_price != null;
  const isProfit = pnl >= 0;

  // ── No SL/TP: simplified card ──
  if (!hasSLTP || !entry_price || !current_price) {
    return (
      <div className="card-base p-3 space-y-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className={`text-[10px] font-mono font-bold ${isLong ? 'text-arena-green' : 'text-arena-red'}`}>
              {side.toUpperCase()}
            </span>
            <span className="text-[11px] font-mono font-semibold text-white">{symbol}</span>
            {agent_name && (
              <span className="text-[9px] text-arena-text-dim">{agent_name}</span>
            )}
          </div>
          <span className={`text-[11px] font-mono ${isProfit ? 'text-arena-green' : 'text-arena-red'}`}>
            {isProfit ? '+' : ''}{pnl_pct.toFixed(1)}%
          </span>
        </div>
        <div className="flex items-center justify-between text-[10px] text-arena-text-dim font-mono">
          <span>Entry: ${entry_price?.toFixed(2) ?? '-'}</span>
          <span>Current: ${current_price?.toFixed(2) ?? '-'}</span>
        </div>
      </div>
    );
  }

  // ── Compute bar geometry ──
  const sl = stop_loss_price ?? entry_price;
  const tp = take_profit_price ?? entry_price;

  // For longs: SL is left (lower), TP is right (higher)
  // For shorts: SL is right (higher), TP is left (lower) — mirror the bar
  const low = Math.min(sl, tp, entry_price, current_price);
  const high = Math.max(sl, tp, entry_price, current_price);
  const range = high - low || 1;

  const pct = (val: number) => ((val - low) / range) * 100;

  // For longs: SL on left, TP on right. For shorts: flip so SL is on right, TP on left.
  const slPct = isLong ? pct(sl) : 100 - pct(sl);
  const tpPct = isLong ? pct(tp) : 100 - pct(tp);
  const entryPct = isLong ? pct(entry_price) : 100 - pct(entry_price);
  const currPct = isLong ? pct(current_price) : 100 - pct(current_price);

  const clampedCurr = Math.max(2, Math.min(98, currPct));

  // Progress: how far from entry toward TP (0-100%)
  const distToTP = Math.abs(tp - entry_price);
  const distFromEntry = Math.abs(current_price - entry_price);
  const progressToTP = distToTP > 0 ? (distFromEntry / distToTP) * 100 : 0;
  const progressToSL = Math.abs(sl - entry_price) > 0
    ? (distFromEntry / Math.abs(sl - entry_price)) * 100
    : 0;

  // Danger zone: within 15% of SL
  const nearSL = progressToSL > 85;
  const nearTP = progressToTP > 85;

  const currColor = isProfit ? '#10B981' : '#EF4444';

  if (compact) {
    return (
      <div className="space-y-1">
        <div className="flex items-center justify-between text-[10px]">
          <div className="flex items-center gap-1.5">
            <span className={`font-mono font-bold ${isLong ? 'text-arena-green' : 'text-arena-red'}`}>
              {side.toUpperCase()}
            </span>
            <span className="font-mono text-white">{symbol}</span>
          </div>
          <span className={`font-mono ${isProfit ? 'text-arena-green' : 'text-arena-red'}`}>
            {isProfit ? '+' : ''}{pnl_pct.toFixed(1)}%
          </span>
        </div>
        <div className="relative h-2 bg-arena-bg rounded-full overflow-hidden">
          {/* Loss zone */}
          <div
            className="absolute h-full bg-arena-red/20"
            style={{
              left: isLong ? '0%' : `${entryPct}%`,
              width: isLong ? `${entryPct}%` : `${100 - entryPct}%`,
            }}
          />
          {/* Profit zone */}
          <div
            className="absolute h-full bg-arena-green/20"
            style={{
              left: isLong ? `${entryPct}%` : '0%',
              width: isLong ? `${100 - entryPct}%` : `${entryPct}%`,
            }}
          />
          {/* SL tick */}
          <div className="absolute top-0 bottom-0 w-px bg-arena-red/60" style={{ left: `${slPct}%` }} />
          {/* TP tick */}
          <div className="absolute top-0 bottom-0 w-px bg-arena-green/60" style={{ left: `${tpPct}%` }} />
          {/* Current price dot */}
          <motion.div
            className="absolute top-1/2 -translate-y-1/2 w-2 h-2 rounded-full"
            style={{ backgroundColor: currColor, boxShadow: `0 0 6px ${currColor}` }}
            animate={{ left: `${clampedCurr}%` }}
            transition={{ type: 'spring', stiffness: 120, damping: 20 }}
          />
        </div>
        <div className="flex items-center justify-between text-[9px] text-arena-text-dim font-mono">
          <span className="text-arena-red/80">${sl.toFixed(2)}</span>
          <span className={nearSL ? 'text-arena-red' : nearTP ? 'text-arena-green' : 'text-arena-text-dim'}>
            {nearSL ? 'near SL' : `${Math.min(progressToTP, 100).toFixed(0)}% to TP`}
          </span>
          <span className="text-arena-green/80">${tp.toFixed(2)}</span>
        </div>
      </div>
    );
  }

  // ── Full card ──
  return (
    <div className="card-base card-hover p-4 space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className={`text-[10px] font-mono font-bold px-1.5 py-0.5 rounded ${isLong ? 'text-arena-green bg-arena-green/10' : 'text-arena-red bg-arena-red/10'}`}>
            {side.toUpperCase()}
          </span>
          <span className="text-sm font-mono font-semibold text-white">{symbol}</span>
          {agent_name && (
            <span className="text-[10px] text-arena-text-dim">{agent_name}</span>
          )}
        </div>
        <div className="text-right">
          <div className={`text-sm font-mono font-bold ${isProfit ? 'text-arena-green' : 'text-arena-red'}`}>
            {isProfit ? '+' : ''}${Math.abs(pnl).toFixed(2)}
          </div>
          <div className={`text-[10px] font-mono ${isProfit ? 'text-arena-green' : 'text-arena-red'}`}>
            {isProfit ? '+' : ''}{pnl_pct.toFixed(1)}%
          </div>
        </div>
      </div>

      {/* SL/TP Progress Bar */}
      <div className="space-y-1.5">
        {/* Labels above bar */}
        <div className="flex items-center justify-between text-[10px] font-mono">
          <span className="text-arena-red/80">
            SL ${sl.toFixed(2)}
          </span>
          <span className={`font-semibold ${nearSL ? 'text-arena-red animate-pulse' : nearTP ? 'text-arena-green' : 'text-arena-text-dim'}`}>
            {nearSL
              ? `near SL — ${Math.min(progressToSL, 100).toFixed(0)}%`
              : `${Math.min(progressToTP, 100).toFixed(0)}% to TP`}
          </span>
          <span className="text-arena-green/80">
            TP ${tp.toFixed(2)}
          </span>
        </div>

        {/* The bar */}
        <div className="relative h-6 bg-arena-bg rounded-lg overflow-hidden border border-arena-border">
          {/* Loss zone (red gradient) */}
          <div
            className="absolute h-full"
            style={{
              left: isLong ? '0%' : `${entryPct}%`,
              width: isLong ? `${entryPct}%` : `${100 - entryPct}%`,
              background: 'linear-gradient(90deg, rgba(239,68,68,0.15), rgba(239,68,68,0.05))',
            }}
          />
          {/* Profit zone (green gradient) */}
          <div
            className="absolute h-full"
            style={{
              left: isLong ? `${entryPct}%` : '0%',
              width: isLong ? `${100 - entryPct}%` : `${entryPct}%`,
              background: 'linear-gradient(90deg, rgba(16,185,129,0.05), rgba(16,185,129,0.15))',
            }}
          />

          {/* SL tick line + label */}
          <div
            className="absolute top-0 bottom-0 w-0.5 bg-arena-red/50"
            style={{ left: `${slPct}%` }}
          />
          <div
            className="absolute top-0 text-[8px] font-mono text-arena-red/70 -translate-x-1/2"
            style={{ left: `${slPct}%` }}
          >
            SL
          </div>

          {/* Entry tick line */}
          <div
            className="absolute top-0 bottom-0 w-0.5 bg-white/15"
            style={{ left: `${entryPct}%` }}
          />
          <div
            className="absolute bottom-0 text-[8px] font-mono text-arena-text-dim -translate-x-1/2"
            style={{ left: `${entryPct}%` }}
          >
            E
          </div>

          {/* TP tick line + label */}
          <div
            className="absolute top-0 bottom-0 w-0.5 bg-arena-green/50"
            style={{ left: `${tpPct}%` }}
          />
          <div
            className="absolute top-0 text-[8px] font-mono text-arena-green/70 -translate-x-1/2"
            style={{ left: `${tpPct}%` }}
          >
            TP
          </div>

          {/* Current price marker — animated dot with glow */}
          <motion.div
            className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 z-10"
            animate={{ left: `${clampedCurr}%` }}
            transition={{ type: 'spring', stiffness: 120, damping: 20 }}
          >
            <div
              className={`w-3 h-3 rounded-full ${nearSL ? 'animate-pulse' : ''}`}
              style={{
                backgroundColor: currColor,
                boxShadow: `0 0 8px ${currColor}, 0 0 16px ${currColor}40`,
                border: '1.5px solid rgba(255,255,255,0.3)',
              }}
            />
          </motion.div>
        </div>

        {/* Price labels below bar */}
        <div className="flex items-center justify-between text-[9px] font-mono text-arena-text-dim">
          <span>${low.toFixed(2)}</span>
          <span className="text-white/60">
            Entry ${entry_price.toFixed(2)} → Current ${current_price.toFixed(2)}
          </span>
          <span>${high.toFixed(2)}</span>
        </div>
      </div>

      {/* Footer stats */}
      <div className="flex items-center justify-between pt-2 border-t border-arena-border text-[10px] font-mono text-arena-text-dim">
        <span>Qty: {Math.abs(quantity ?? 0).toFixed(4)}</span>
        {opened_at && (
          <span>Opened: {formatDate(opened_at)}</span>
        )}
      </div>
    </div>
  );
}

function formatDate(ts: string): string {
  try {
    return new Date(ts).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}
