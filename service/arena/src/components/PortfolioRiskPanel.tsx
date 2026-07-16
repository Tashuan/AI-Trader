import { useState, useEffect } from 'react';
import { Info } from 'lucide-react';
import type { PortfolioRiskData } from '../types';
import { Tooltip } from './Tooltip';

interface Props {
  data: PortfolioRiskData | null;
  onUnhalt: () => Promise<void>;
  isAdmin?: boolean;
  lastUpdated?: number | null;
}

const GROSS_EXPOSURE_TOOLTIP = 'Calculated as Gross Exposure (Absolute Sum of Longs + Shorts) to measure total capital at risk.';

function getInitials(name: string): string {
  const parts = name.trim().split(/\s+/);
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function useRelativeTime(timestamp: number | null | undefined): string {
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const interval = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(interval);
  }, []);

  if (!timestamp) return 'never';
  const seconds = Math.floor((now - timestamp) / 1000);
  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ago`;
}

export function PortfolioRiskPanel({ data, onUnhalt, isAdmin = false, lastUpdated }: Props) {
  const [unhalting, setUnhalting] = useState(false);
  const relativeTime = useRelativeTime(lastUpdated);

  if (!data) {
    return (
      <div className="bg-arena-card border border-arena-border rounded-lg p-4">
        <div className="text-xs text-arena-text-dim animate-pulse">Loading portfolio risk...</div>
      </div>
    );
  }

  const isHalted = data.halted === 1;
  const isDailyLoss = data.daily_pnl_pct < 0;
  const t = data.thresholds;
  const maxDailyLoss = t?.max_daily_loss_pct ?? 0.05;
  const maxSymbolPct = t?.max_symbol_pct ?? 0.35;
  const maxSectorPct = t?.max_sector_pct ?? 0.50;
  const maxUnknownPct = t?.max_unknown_pct ?? 0.10;
  const maxCrowding = t?.max_crowding ?? 3;
  const drawdownPct = isDailyLoss ? Math.abs(data.daily_pnl_pct) : 0;
  const drawdownBarPct = Math.min(drawdownPct / maxDailyLoss * 100, 100);

  const fmtPct = (v: number) => `${(v * 100).toFixed(1)}%`;
  const fmtUsd = (v: number) => `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;

  const sortedSymbols = Object.entries(data.symbol_exposure ?? {})
    .sort((a, b) => b[1].value - a[1].value)
    .slice(0, 8);

  const sortedSectors = Object.entries(data.sector_exposure ?? {})
    .sort((a, b) => b[1].value - a[1].value);

  const crowdedSymbols = Object.entries(data.crowding ?? {})
    .filter(([, agents]) => agents.length >= maxCrowding - 1)
    .sort((a, b) => b[1].length - a[1].length);

  const hasPositions = sortedSymbols.length > 0 || sortedSectors.length > 0;

  const handleUnhalt = async () => {
    setUnhalting(true);
    try {
      await onUnhalt();
    } finally {
      setUnhalting(false);
    }
  };

  function getExposureState(pct: number, cap: number): 'normal' | 'warning' | 'breached' {
    if (pct > cap) return 'breached';
    if (pct >= cap * 0.8) return 'warning';
    return 'normal';
  }

  function exposureTextClass(state: 'normal' | 'warning' | 'breached'): string {
    if (state === 'breached') return 'text-red-400 font-bold';
    if (state === 'warning') return 'text-amber-400 font-medium';
    return 'text-arena-text';
  }

  function exposureRowClass(state: 'normal' | 'warning' | 'breached'): string {
    if (state === 'breached') return 'bg-red-500/5 rounded px-1 -mx-1';
    if (state === 'warning') return 'bg-amber-500/5 rounded px-1 -mx-1';
    return '';
  }

  return (
    <div className="bg-arena-card border border-arena-border rounded-lg p-4 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {!isHalted && (
            <span className="inline-block w-2 h-2 rounded-full bg-green-400 animate-pulse" />
          )}
          <h3 className="text-sm font-bold text-white">Portfolio Risk Engine</h3>
        </div>
        <div className={`px-2 py-0.5 rounded text-xs font-medium ${
          isHalted
            ? 'bg-red-500/20 text-red-400 border border-red-500/30'
            : 'bg-green-500/20 text-green-400 border border-green-500/30'
        }`}>
          {isHalted ? 'HALTED' : 'ACTIVE'}
        </div>
      </div>

      {/* Halt banner */}
      {isHalted && (
        <div className="bg-red-500/10 border border-red-500/20 rounded-md p-3">
          <div className="text-xs text-red-400 font-medium mb-1">Trading Halted</div>
          <div className="text-xs text-arena-text-dim">{data.halt_reason}</div>
          {isAdmin ? (
            <button
              onClick={handleUnhalt}
              disabled={unhalting}
              className="mt-2 px-3 py-1 text-xs bg-red-500/20 hover:bg-red-500/30 border border-red-500/30 text-red-400 rounded transition-colors disabled:opacity-50"
            >
              {unhalting ? 'Un-halting...' : 'Un-halt (Admin)'}
            </button>
          ) : (
            <Tooltip text="Admin privileges required to un-halt." side="bottom">
              <button
                disabled
                className="mt-2 px-3 py-1 text-xs bg-red-500/10 border border-red-500/20 text-red-400/50 rounded opacity-50 cursor-not-allowed"
              >
                Un-halt (Admin)
              </button>
            </Tooltip>
          )}
        </div>
      )}

      {/* Graceful empty state */}
      {!hasPositions && (
        <div className="text-center py-8">
          <div className="text-xs text-arena-text-dim">No active positions. Portfolio risk engine standing by.</div>
        </div>
      )}

      {/* Equity summary — only when positions exist */}
      {hasPositions && (
        <div className="grid grid-cols-2 gap-2">
          <div className="bg-arena-bg/50 rounded-md p-2">
            <div className="text-xs text-arena-text-dim">Total Equity</div>
            <div className="text-sm font-bold text-white">{fmtUsd(data.total_equity)}</div>
          </div>
          <div className="bg-arena-bg/50 rounded-md p-2">
            <div className="text-xs text-arena-text-dim">Starting Equity</div>
            <div className="text-sm font-bold text-white">{fmtUsd(data.starting_equity)}</div>
          </div>
        </div>
      )}

      {/* Daily P&L with Drawdown Bar — only when positions exist */}
      {hasPositions && (
        <div className="bg-arena-bg/50 rounded-md p-2">
          <div className="flex items-center justify-between">
            <span className="text-xs text-arena-text-dim">Daily P&L</span>
            <span className={`text-sm font-bold ${
              isDailyLoss ? 'text-red-400' : 'text-green-400'
            }`}>
              {isDailyLoss ? '-' : '+'}{fmtUsd(Math.abs(data.daily_pnl))} ({fmtPct(Math.abs(data.daily_pnl_pct))})
            </span>
          </div>
          {/* Drawdown to Halt bar */}
          <div className="mt-2">
            <div className="flex items-center justify-between text-[10px] text-arena-text-dim mb-0.5">
              <span>Drawdown to Halt</span>
              <span className={isDailyLoss ? 'text-red-400' : 'text-arena-text-dim'}>
                {isDailyLoss ? `${fmtPct(drawdownPct)} / ${fmtPct(maxDailyLoss)}` : `0% / ${fmtPct(maxDailyLoss)}`}
              </span>
            </div>
            <div className="h-1.5 bg-arena-bg rounded-full overflow-hidden">
              <div
                className="h-full rounded-full bg-red-500 transition-all duration-300"
                style={{ width: `${drawdownBarPct}%` }}
              />
            </div>
          </div>
        </div>
      )}

      {/* Symbol concentration — only when positions exist */}
      {hasPositions && (
        <div>
          <div className="flex items-center gap-1 mb-1.5">
            <span className="text-xs text-arena-text-dim">Symbol Exposure (top 8)</span>
            <Tooltip text={GROSS_EXPOSURE_TOOLTIP} side="right">
              <Info size={12} className="text-arena-text-dim cursor-help" />
            </Tooltip>
          </div>
          <div className="space-y-1">
            {sortedSymbols.map(([symbol, info]) => {
              const state = getExposureState(info.pct, maxSymbolPct);
              return (
                <div key={symbol} className={`flex items-center justify-between text-xs ${exposureRowClass(state)}`}>
                  <span className="text-white font-medium">{symbol}</span>
                  <div className="flex items-center gap-2">
                    <span className="text-arena-text-dim">{info.agents} agent{info.agents !== 1 ? 's' : ''}</span>
                    <span className={exposureTextClass(state)}>
                      {fmtPct(info.pct)}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
          <div className="text-xs text-arena-text-dim mt-1">Limit: {fmtPct(maxSymbolPct)}</div>
        </div>
      )}

      {/* Sector concentration — only when positions exist */}
      {hasPositions && (
        <div>
          <div className="flex items-center gap-1 mb-1.5">
            <span className="text-xs text-arena-text-dim">Sector Exposure</span>
            <Tooltip text={GROSS_EXPOSURE_TOOLTIP} side="right">
              <Info size={12} className="text-arena-text-dim cursor-help" />
            </Tooltip>
          </div>
          <div className="space-y-1">
            {sortedSectors.map(([sector, info]) => {
              const cap = sector === 'unknown'
                ? maxUnknownPct
                : maxSectorPct;
              const state = getExposureState(info.pct, cap);
              return (
                <div key={sector} className={`flex items-center justify-between text-xs ${exposureRowClass(state)}`}>
                  <span className="text-white font-medium capitalize">{sector}</span>
                  <span className={exposureTextClass(state)}>
                    {fmtPct(info.pct)}
                  </span>
                </div>
              );
            })}
          </div>
          <div className="text-xs text-arena-text-dim mt-1">
            Limits: {fmtPct(maxSectorPct)} / unknown {fmtPct(maxUnknownPct)}
          </div>
        </div>
      )}

      {/* Crowding — compact pills */}
      {crowdedSymbols.length > 0 && hasPositions && (
        <div>
          <div className="text-xs text-arena-text-dim mb-1.5">Crowding Warnings</div>
          <div className="space-y-1.5">
            {crowdedSymbols.map(([symbol, agents]) => {
              const longCount = agents.filter(a => a.side.toLowerCase() === 'long').length;
              const shortCount = agents.filter(a => a.side.toLowerCase() === 'short').length;
              const herdDir = longCount > shortCount ? 'long' : shortCount > longCount ? 'short' : 'mixed';
              return (
                <div key={symbol} className="flex items-center gap-1.5 text-xs flex-wrap">
                  <span className="text-yellow-400 font-medium">{symbol}</span>
                  <div className="flex items-center gap-0.5 flex-wrap">
                    {agents.map((a, i) => (
                      <Tooltip key={i} text={`${a.agent} (${a.side})`} side="top">
                        <span className={`inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded text-[9px] font-bold ${
                          a.side.toLowerCase() === 'long'
                            ? 'bg-green-500/15 text-green-400'
                            : 'bg-red-500/15 text-red-400'
                        }`}>
                          {getInitials(a.agent)}
                        </span>
                      </Tooltip>
                    ))}
                  </div>
                  {herdDir === 'long' && <span className="text-green-400 text-[10px]">📈</span>}
                  {herdDir === 'short' && <span className="text-red-400 text-[10px]">📉</span>}
                </div>
              );
            })}
          </div>
          <div className="text-xs text-arena-text-dim mt-1">Limit: {maxCrowding} agents per symbol+side</div>
        </div>
      )}

      {/* Freshness indicator */}
      <div className="text-[10px] text-arena-text-dim border-t border-arena-border pt-2">
        Last updated: {relativeTime}
      </div>
    </div>
  );
}
