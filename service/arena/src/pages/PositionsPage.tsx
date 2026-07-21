import { useState, useEffect, useCallback } from 'react';
import { Filter, TrendingUp, TrendingDown, Target } from 'lucide-react';
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
  const [agentFilter, setAgentFilter] = useState<string>('all');
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

  // Apply filters + sorting
  const filtered = positions
    .filter(pos => {
      if (agentFilter !== 'all' && pos.agent_name !== agentFilter) return false;
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

        {/* Agent filter */}
        <select
          value={agentFilter}
          onChange={e => setAgentFilter(e.target.value)}
          className="form-input w-auto min-w-[120px]"
        >
          <option value="all">All Agents</option>
          {availableAgents.map(a => (
            <option key={a.id} value={a.name}>{a.name}</option>
          ))}
        </select>

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
