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
    trailing_sl_pct,
    trailing_activation_pct,
    peak_favorable_price,
    trailing_activated,
    quantity,
    opened_at,
    agent_name,
  } = position;

  const isTrailing = trailing_activated === true && trailing_sl_pct != null;

  const isLong = side === 'long';
  const hasSLTP = stop_loss_price != null || take_profit_price != null;
  const isProfit = pnl >= 0;

  // ── No SL/TP or no entry_price: simplified card ──
  // (current_price may be null temporarily — we still render the slider using entry_price as fallback)
  if (!hasSLTP || !entry_price) {
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
  // Fall back to entry_price when current_price is null so the slider still renders
  const effectiveCurrent = current_price ?? entry_price;

  // For longs: SL is left (lower), TP is right (higher)
  // For shorts: SL is right (higher), TP is left (lower) — mirror the bar
  const low = Math.min(sl, tp, entry_price, effectiveCurrent);
  const high = Math.max(sl, tp, entry_price, effectiveCurrent);
  const range = high - low || 1;

  const pct = (val: number) => ((val - low) / range) * 100;

  // For longs: SL on left, TP on right. For shorts: flip so SL is on right, TP on left.
  const slPct = isLong ? pct(sl) : 100 - pct(sl);
  const tpPct = isLong ? pct(tp) : 100 - pct(tp);
  const entryPct = isLong ? pct(entry_price) : 100 - pct(entry_price);
  const currPct = isLong ? pct(effectiveCurrent) : 100 - pct(effectiveCurrent);

  const clampedCurr = Math.max(2, Math.min(98, currPct));

  // Progress: how far from entry toward TP (0-100%)
  const distToTP = Math.abs(tp - entry_price);
  const distFromEntry = Math.abs(effectiveCurrent - entry_price);
  const progressToTP = distToTP > 0 ? (distFromEntry / distToTP) * 100 : 0;
  const progressToSL = Math.abs(sl - entry_price) > 0
    ? (distFromEntry / Math.abs(sl - entry_price)) * 100
    : 0;

  // Danger zone: within 15% of SL
  const nearSL = progressToSL > 85;
  const nearTP = progressToTP > 85;

  const currColor = isProfit ? '#10B981' : '#EF4444';

  // ── Potential profit / loss from TP / SL ──
  const absQty = Math.abs(quantity ?? 0);
  const potentialProfit = absQty > 0 ? Math.abs(tp - entry_price) * absQty : 0;
  const potentialLoss = absQty > 0 ? Math.abs(sl - entry_price) * absQty : 0;

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
        {absQty > 0 && (
          <div className="flex items-center justify-between text-[9px] font-mono">
            <span className="text-arena-red/70">Risk: ${potentialLoss.toFixed(2)}</span>
            <span className="text-arena-green/70">Reward: ${potentialProfit.toFixed(2)}</span>
          </div>
        )}
      </div>
    );
  }

  // ── Full card ──
  return (
    <div className="card-base card-hover p-4 space-y-3">
      {/* Header — symbol as anchor, P&L as hero number */}
      <div className="flex items-start justify-between">
        <div className="flex flex-col gap-0.5">
          <div className="flex items-center gap-2">
            <span className={`text-[10px] font-mono font-bold px-1.5 py-0.5 rounded ${isLong ? 'text-arena-green bg-arena-green/10' : 'text-arena-red bg-arena-red/10'}`}>
              {side.toUpperCase()}
            </span>
            <span className="text-sm font-mono font-semibold text-white">{symbol}</span>
          </div>
          {agent_name && (
            <span className="text-[10px] text-arena-text-dim ml-1">by {agent_name}</span>
          )}
        </div>
        <div className="text-right">
          <div className={`text-base font-mono font-bold leading-tight ${isProfit ? 'text-arena-green' : 'text-arena-red'}`}>
            {isProfit ? '+' : '-'}${Math.abs(pnl).toFixed(2)}
          </div>
          <div className={`text-[10px] font-mono ${isProfit ? 'text-arena-green' : 'text-arena-red'}`}>
            {isProfit ? '+' : ''}{pnl_pct.toFixed(1)}%
          </div>
        </div>
      </div>

      {/* Key metrics — 3-col grid */}
      <div className="grid grid-cols-3 gap-2 text-[10px] font-mono">
        <div>
          <div className="text-arena-text-dim mb-0.5">Entry</div>
          <div className="text-white/80">${entry_price?.toFixed(2) ?? '-'}</div>
        </div>
        <div className="text-center">
          <div className="text-arena-text-dim mb-0.5">Current</div>
          <div className={isProfit ? 'text-arena-green' : 'text-arena-red'}>
            ${current_price?.toFixed(2) ?? '-'}
          </div>
        </div>
        <div className="text-right">
          <div className="text-arena-text-dim mb-0.5">Qty</div>
          <div className="text-white/80">{absQty > 0 ? absQty.toFixed(4) : '-'}</div>
        </div>
      </div>

      {/* SL/TP Progress Bar — kept as-is */}
      <div className="space-y-1.5">
        {/* Labels above bar */}
        <div className="flex items-center justify-between text-[10px] font-mono">
          <span className="text-arena-red/80 flex items-center gap-1">
            SL ${sl.toFixed(2)}
            {isTrailing && (
              <span className="text-[8px] font-bold text-arena-purple bg-arena-purple/10 px-1 py-0.5 rounded">TRAIL</span>
            )}
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

        {/* Range labels below bar — simplified */}
        <div className="flex items-center justify-between text-[9px] font-mono text-arena-text-dim">
          <span>${low.toFixed(2)}</span>
          <span>${high.toFixed(2)}</span>
        </div>
      </div>

      {/* Footer — trailing info + risk/reward + opened date */}
      <div className="pt-2 border-t border-arena-border space-y-1">
        {isTrailing && (
          <div className="flex items-center justify-between text-[9px] font-mono">
            <span className="text-arena-purple/80">Trail: {trailing_sl_pct?.toFixed(1)}% below peak</span>
            {peak_favorable_price != null && (
              <span className="text-arena-text-dim">Peak: ${peak_favorable_price.toFixed(2)}</span>
            )}
          </div>
        )}
        {!isTrailing && trailing_sl_pct != null && trailing_activation_pct != null && (
          <div className="flex items-center justify-between text-[9px] font-mono">
            <span className="text-arena-text-dim">Trailing {trailing_sl_pct.toFixed(1)}% — activates at +{trailing_activation_pct.toFixed(1)}%</span>
          </div>
        )}
        {absQty > 0 && (
          <div className="flex items-center justify-between text-[10px] font-mono">
            <span className="text-arena-red/80">Risk -${potentialLoss.toFixed(2)}</span>
            <span className="text-arena-green/80">Reward +${potentialProfit.toFixed(2)}</span>
          </div>
        )}
        {opened_at && (
          <div className="text-[9px] font-mono text-arena-text-dim">
            Opened {formatDate(opened_at)}
          </div>
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
