import { useState, useEffect, useCallback, useRef } from 'react';
import { Filter, TrendingUp, TrendingDown, Target, Check, ChevronDown } from 'lucide-react';
import { PositionTracker } from '../components/PositionTracker';
import type { AgentPosition } from '../types';

const API_BASE = '/api';
const REFRESH_INTERVAL = 5000;

interface PositionsResponse {
  positions: AgentPosition[];
  count: number;
}

export function PositionsPage() {
  const [positions, setPositions] = useState<AgentPosition[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [agentFilter, setAgentFilter] = useState<string[]>([]);
  const [agentDropdownOpen, setAgentDropdownOpen] = useState(false);
  const agentDropdownRef = useRef<HTMLDivElement>(null);
  const [sideFilter, setSideFilter] = useState<string>('all');
  const [sortBy, setSortBy] = useState<string>('pnl');
  const [availableAgents, setAvailableAgents] = useState<{ id: number; name: string }[]>([]);

  const fetchPositions = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}/arena/positions`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data: PositionsResponse = await resp.json();
      setPositions(data.positions || []);
      setError(null);

      // Extract unique agents for filter dropdown
      const agentMap = new Map<number, string>();
      for (const pos of data.positions) {
        if (pos.agent_id && pos.agent_name) {
          agentMap.set(pos.agent_id, pos.agent_name);
        }
      }
      setAvailableAgents(Array.from(agentMap.entries()).map(([id, name]) => ({ id, name })));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch positions');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPositions();
    const interval = setInterval(fetchPositions, REFRESH_INTERVAL);
    return () => clearInterval(interval);
  }, [fetchPositions]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (agentDropdownRef.current && !agentDropdownRef.current.contains(e.target as Node)) {
        setAgentDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  // Apply filters + sorting
  const filtered = positions
    .filter(pos => {
      if (agentFilter.length > 0 && !agentFilter.includes(pos.agent_name ?? '')) return false;
      if (sideFilter !== 'all' && pos.side !== sideFilter) return false;
      return true;
    })
    .sort((a, b) => {
      switch (sortBy) {
        case 'pnl':
          return (b.pnl ?? 0) - (a.pnl ?? 0);
        case 'time':
          return new Date(b.opened_at ?? 0).getTime() - new Date(a.opened_at ?? 0).getTime();
        case 'symbol':
          return (a.symbol ?? '').localeCompare(b.symbol ?? '');
        default:
          return 0;
      }
    });

  const totalPnl = filtered.reduce((sum, p) => sum + (p.pnl ?? 0), 0);
  const profitCount = filtered.filter(p => (p.pnl ?? 0) >= 0).length;
  const lossCount = filtered.length - profitCount;

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-sm text-arena-text-dim animate-pulse">Loading positions...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center">
          <div className="text-sm text-arena-red mb-2">Failed to load positions</div>
          <div className="text-xs text-arena-text-dim">{error}</div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-4">
      {/* Header */}
      <div className="mb-4">
        <div className="flex items-center gap-2 mb-1">
          <Target size={16} className="text-arena-purple" />
          <h1 className="text-base font-bold text-white">Position Tracker</h1>
        </div>
        <p className="text-[11px] text-arena-text-dim">Live SL/TP tracking across all agent positions</p>
      </div>

      {/* Summary stats */}
      <div className="flex items-center gap-4 mb-4 text-[11px]">
        <div className="flex items-center gap-1.5">
          <span className="text-arena-text-dim">Total:</span>
          <span className="font-mono font-semibold text-white">{filtered.length}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <TrendingUp size={12} className="text-arena-green" />
          <span className="font-mono text-arena-green">{profitCount}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <TrendingDown size={12} className="text-arena-red" />
          <span className="font-mono text-arena-red">{lossCount}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-arena-text-dim">Unrealized P&L:</span>
          <span className={`font-mono font-semibold ${totalPnl >= 0 ? 'text-arena-green' : 'text-arena-red'}`}>
            {totalPnl >= 0 ? '+' : ''}${Math.abs(totalPnl).toFixed(2)}
          </span>
        </div>
      </div>

      {/* Filter bar */}
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <div className="flex items-center gap-1.5">
          <Filter size={12} className="text-arena-text-dim" />
        </div>

        {/* Agent filter — multi-select */}
        <div className="relative" ref={agentDropdownRef}>
          <button
            onClick={() => setAgentDropdownOpen(o => !o)}
            className="form-input w-auto min-w-[120px] flex items-center justify-between gap-1.5 cursor-pointer"
          >
            <span className="truncate">
              {agentFilter.length === 0
                ? 'All Agents'
                : agentFilter.length === 1
                  ? agentFilter[0]
                  : `${agentFilter.length} agents`}
            </span>
            <ChevronDown size={12} className={`text-arena-text-dim shrink-0 transition-transform ${agentDropdownOpen ? 'rotate-180' : ''}`} />
          </button>
          {agentDropdownOpen && (
            <div className="absolute top-full left-0 mt-1 z-20 min-w-[160px] card-base p-1 shadow-xl">
              {availableAgents.map(a => {
                const checked = agentFilter.includes(a.name);
                return (
                  <button
                    key={a.id}
                    onClick={() => {
                      setAgentFilter(prev =>
                        checked
                          ? prev.filter(n => n !== a.name)
                          : [...prev, a.name],
                      );
                    }}
                    className="w-full flex items-center gap-2 px-2 py-1.5 rounded hover:bg-white/5 text-left"
                  >
                    <span className={`w-3.5 h-3.5 rounded border flex items-center justify-center shrink-0 ${checked ? 'bg-arena-purple border-arena-purple' : 'border-white/20'}`}>
                      {checked && <Check size={10} className="text-white" />}
                    </span>
                    <span className="text-[11px] text-white/80 truncate">{a.name}</span>
                  </button>
                );
              })}
              {agentFilter.length > 0 && (
                <button
                  onClick={() => setAgentFilter([])}
                  className="w-full text-[10px] text-arena-text-dim hover:text-white px-2 py-1.5 border-t border-arena-border mt-1"
                >
                  Clear
                </button>
              )}
            </div>
          )}
        </div>

        {/* Side filter */}
        <select
          value={sideFilter}
          onChange={e => setSideFilter(e.target.value)}
          className="form-input w-auto min-w-[90px]"
        >
          <option value="all">All Sides</option>
          <option value="long">Long</option>
          <option value="short">Short</option>
        </select>

        {/* Sort */}
        <select
          value={sortBy}
          onChange={e => setSortBy(e.target.value)}
          className="form-input w-auto min-w-[110px]"
        >
          <option value="pnl">Sort: P&L</option>
          <option value="time">Sort: Time</option>
          <option value="symbol">Sort: Symbol</option>
        </select>
      </div>

      {/* Positions grid */}
      {filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20">
          <div className="text-3xl mb-2 opacity-30">📊</div>
          <div className="text-sm text-arena-text-dim">No open positions match your filters</div>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-3">
          {filtered.map((pos, i) => (
            <PositionTracker key={`${pos.agent_id}-${pos.symbol}-${i}`} position={pos} />
          ))}
        </div>
      )}
    </div>
  );
}
