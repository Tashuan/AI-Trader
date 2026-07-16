import { motion, AnimatePresence } from 'framer-motion';
import { useState, useMemo } from 'react';
import type { Agent } from '../types';
import { AgentCard } from './AgentCard';

interface AgentArenaProps {
  agents: Agent[];
  mentionedAgent: string | null;
  onAgentClick: (agent: Agent) => void;
}

type FilterMode = 'all' | 'active' | 'inactive';
type SortMode = 'default' | 'name' | 'pnl' | 'pnl_pct' | 'trades' | 'win_rate';

const SORT_LABELS: Record<SortMode, string> = {
  default: 'Default (Active first)',
  name: 'Name (A–Z)',
  pnl: 'Total P&L ($)',
  pnl_pct: 'Total P&L (%)',
  trades: 'Trade Count',
  win_rate: 'Win Rate',
};

export function AgentArena({ agents, mentionedAgent, onAgentClick }: AgentArenaProps) {
  const [filter, setFilter] = useState<FilterMode>('all');
  const [sort, setSort] = useState<SortMode>('default');

  const filteredAndSorted = useMemo(() => {
    let result = [...agents];

    // Filter
    if (filter !== 'all') {
      result = result.filter(a => {
        const isActive = a.online || a.bot_running;
        return filter === 'active' ? isActive : !isActive;
      });
    }

    // Sort
    result.sort((a, b) => {
      switch (sort) {
        case 'name':
          return a.name.localeCompare(b.name);
        case 'pnl':
          return b.total_profit - a.total_profit;
        case 'pnl_pct':
          return (b.total_profit / 100000) - (a.total_profit / 100000);
        case 'trades':
          return b.trade_count - a.trade_count;
        case 'win_rate':
          return b.win_rate - a.win_rate;
        default: {
          const aActive = a.online || a.bot_running;
          const bActive = b.online || b.bot_running;
          if (aActive !== bActive) return aActive ? -1 : 1;
          const aIdle = a.state === 'idle';
          const bIdle = b.state === 'idle';
          if (aIdle !== bIdle) return aIdle ? 1 : -1;
          return a.name.localeCompare(b.name);
        }
      }
    });

    return result;
  }, [agents, filter, sort]);

  const activeCount = agents.filter(a => a.online || a.bot_running).length;
  const inactiveCount = agents.length - activeCount;

  return (
    <div className="flex-1 overflow-y-auto p-4">
      {/* Filter & Sort Controls */}
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        {/* Filter buttons */}
        <div className="flex items-center gap-1 bg-arena-card border border-arena-border rounded-lg p-0.5">
          {([
            { key: 'all' as FilterMode, label: 'All', count: agents.length },
            { key: 'active' as FilterMode, label: 'Active', count: activeCount },
            { key: 'inactive' as FilterMode, label: 'Inactive', count: inactiveCount },
          ]).map(({ key, label, count }) => (
            <button
              key={key}
              onClick={() => setFilter(key)}
              className={`px-2.5 py-1 text-[10px] font-semibold rounded-md transition-colors ${
                filter === key
                  ? 'bg-arena-blue text-white'
                  : 'text-arena-text-dim hover:text-white'
              }`}
            >
              {label} <span className="opacity-60">({count})</span>
            </button>
          ))}
        </div>

        {/* Sort dropdown */}
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] text-arena-text-dim">Sort by</span>
          <select
            value={sort}
            onChange={e => setSort(e.target.value as SortMode)}
            className="bg-arena-card border border-arena-border rounded-lg px-2 py-1 text-[10px] text-white outline-none cursor-pointer hover:border-arena-border-hover transition-colors"
          >
            {(Object.keys(SORT_LABELS) as SortMode[]).map(key => (
              <option key={key} value={key}>{SORT_LABELS[key]}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Agent Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <AnimatePresence>
          {filteredAndSorted.map(agent => (
            <motion.div
              key={agent.agent_id}
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.9 }}
              transition={{ duration: 0.3 }}
            >
              <AgentCard
                agent={agent}
                mentioned={mentionedAgent === agent.name}
                onClick={() => onAgentClick(agent)}
              />
            </motion.div>
          ))}
        </AnimatePresence>
      </div>

      {filteredAndSorted.length === 0 && (
        <div className="flex items-center justify-center h-full">
          <div className="text-arena-text-dim text-sm">
            {agents.length === 0
              ? 'No agents in the arena yet...'
              : `No ${filter} agents found`}
          </div>
        </div>
      )}
    </div>
  );
}
