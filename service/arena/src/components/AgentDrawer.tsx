import { useEffect, useState } from 'react';
import { X, TrendingUp, TrendingDown, Brain, Users, Target } from 'lucide-react';
import type { Agent } from '../types';
import { GrowthChart } from './GrowthChart';
import { PositionTracker } from './PositionTracker';

interface AgentDrawerProps {
  agent: Agent | null;
  onClose: () => void;
}

interface AgentDetail {
  agent: { id: number; name: string; identity_status: string };
  personality: { tagline: string; bio: string; goal: string; voice: string; quirks: string[]; watchlist: string[] };
  positions: { symbol: string; side: string; quantity: number; entry_price: number; current_price: number; opened_at: string; stop_loss_price: number | null; take_profit_price: number | null; market: string }[];
  trades: { symbol: string; side: string; signal_type: string; pnl: number; content: string; created_at: string }[];
  reasoning: { title: string; content: string; created_at: string }[];
  profit_history: { total_value: number; profit: number; recorded_at: string }[];
  conversations: { content: string; created_at: string; signal_title: string; signal_author: string }[];
  stats: { total_trades: number; winning_trades: number; win_rate: number; current_streak: number; best_streak: number; total_profit: number; max_drawdown: number };
  state: { state: string; detail: string; symbol: string; confidence: number };
  relationships: Record<string, { trust: number; dislike: number; agrees: number; disagrees: number }>;
  memories: { memory_type: string; content: string; symbol: string; impact: number }[];
}

export function AgentDrawer({ agent, onClose }: AgentDrawerProps) {
  const [detail, setDetail] = useState<AgentDetail | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!agent) {
      setDetail(null);
      return;
    }
    let firstLoad = true;
    const fetchDetail = () => {
      fetch(`/api/arena/agent/${agent.agent_id}/detail`)
        .then(r => r.json())
        .then(data => {
          setDetail(data);
          if (firstLoad) {
            setLoading(false);
            firstLoad = false;
          }
        })
        .catch(() => {
          if (firstLoad) setLoading(false);
        });
    };
    setLoading(true);
    fetchDetail();
    const interval = setInterval(fetchDetail, 15000);
    return () => clearInterval(interval);
  }, [agent]);

  if (!agent) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/70" onClick={onClose} />

      <div
        className="relative w-full max-w-md bg-arena-card border-l border-arena-border overflow-y-auto drawer-slide-in"
      >
            {/* Header */}
            <div className="sticky top-0 z-10 bg-arena-card border-b border-arena-border p-4 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="text-lg font-bold text-white">{agent.name}</div>
                <span className="text-[10px] text-arena-text-dim">{agent.tagline}</span>
              </div>
              <button onClick={onClose} className="text-arena-text-dim hover:text-white transition-colors">
                <X size={18} />
              </button>
            </div>

            {loading && (
              <div className="p-8 text-center text-arena-text-dim text-sm">Loading agent details...</div>
            )}

            {detail && !loading && (
              <div className="p-4 space-y-4">
                {/* Bio */}
                <Section title="Profile" icon={<Brain size={12} />}>
                  <p className="text-[11px] text-arena-text-secondary leading-relaxed">{detail.personality?.bio || agent.bio}</p>
                  <div className="mt-2 flex items-center gap-2">
                    <Target size={10} className="text-arena-purple" />
                    <span className="text-[10px] text-arena-purple">{detail.personality?.goal || agent.goal}</span>
                  </div>
                </Section>

                {/* Current State */}
                <Section title="Current State" icon={<TrendingUp size={12} />}>
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-[10px] font-mono font-bold" style={{ color: agent.state_color }}>
                      {(detail.state?.state || agent.state).toUpperCase()}
                    </span>
                    {detail.state?.symbol && (
                      <span className="text-[10px] font-mono text-arena-blue">{detail.state.symbol}</span>
                    )}
                  </div>
                  <p className="text-[11px] text-arena-text-secondary">{detail.state?.detail || agent.state_detail}</p>
                </Section>

                {/* Performance Stats */}
                {detail.stats && Object.keys(detail.stats).length > 0 && (
                  <Section title="Performance Stats" icon={<TrendingUp size={12} />}>
                    <div className="grid grid-cols-2 gap-2 text-[11px]">
                      <Stat label="Total Trades" value={detail.stats.total_trades || 0} />
                      <Stat label="Win Rate" value={`${Math.round((detail.stats.win_rate || 0) * 100)}%`} />
                      <Stat label="Current Streak" value={detail.stats.current_streak > 0 ? `W${detail.stats.current_streak}` : detail.stats.current_streak < 0 ? `L${Math.abs(detail.stats.current_streak)}` : 0} />
                      <Stat label="Best Streak" value={detail.stats.best_streak || 0} />
                      <Stat label="Total P&L" value={`$${(detail.stats.total_profit || 0).toFixed(0)}`} positive={detail.stats.total_profit >= 0} />
                      <Stat label="Max DD" value={`$${(detail.stats.max_drawdown || 0).toFixed(0)}`} negative />
                    </div>
                  </Section>
                )}

                {/* Growth Chart */}
                {detail.profit_history && detail.profit_history.length > 1 && (
                  <Section title="Growth" icon={<TrendingUp size={12} />}>
                    <GrowthChart data={detail.profit_history} height={240} />
                  </Section>
                )}

                {/* Positions */}
                {detail.positions && detail.positions.length > 0 && (
                  <Section title="Open Positions" icon={<TrendingUp size={12} />}>
                    <div className="space-y-2">
                      {detail.positions.map((pos, i) => {
                        const pnl = pos.current_price && pos.entry_price
                          ? (pos.side === 'long'
                            ? (pos.current_price - pos.entry_price) * Math.abs(pos.quantity)
                            : (pos.entry_price - pos.current_price) * Math.abs(pos.quantity))
                          : 0;
                        const pnl_pct = pos.entry_price && pos.entry_price > 0 && pos.quantity
                          ? (pnl / (pos.entry_price * Math.abs(pos.quantity))) * 100
                          : 0;
                        return (
                          <PositionTracker
                            key={i}
                            position={{
                              side: pos.side,
                              symbol: pos.symbol,
                              pnl,
                              pnl_pct,
                              entry_price: pos.entry_price,
                              current_price: pos.current_price,
                              stop_loss_price: pos.stop_loss_price,
                              take_profit_price: pos.take_profit_price,
                              quantity: pos.quantity,
                              opened_at: pos.opened_at,
                              market: pos.market,
                            }}
                          />
                        );
                      })}
                    </div>
                  </Section>
                )}

                {/* Reasoning Log */}
                {detail.reasoning && detail.reasoning.length > 0 && (
                  <Section title="Recent Analysis" icon={<Brain size={12} />}>
                    <div className="space-y-2">
                      {detail.reasoning.slice(0, 5).map((r, i) => (
                        <div key={i} className="text-[11px]">
                          <div className="text-white font-medium">{r.title}</div>
                          <div className="text-arena-text-secondary line-clamp-3 mt-0.5">{r.content}</div>
                          <div className="text-[9px] text-arena-text-dim mt-0.5">{formatDate(r.created_at)}</div>
                        </div>
                      ))}
                    </div>
                  </Section>
                )}

                {/* Relationships */}
                {Object.keys(detail.relationships || {}).length > 0 && (
                  <Section title="Relationships" icon={<Users size={12} />}>
                    <div className="space-y-1.5">
                      {Object.entries(detail.relationships).slice(0, 5).map(([name, rel]) => (
                        <div key={name} className="flex items-center justify-between text-[11px]">
                          <span className="text-white">{name}</span>
                          <div className="flex items-center gap-2">
                            {rel.trust > 0.6 && <span className="text-arena-green text-[9px]">Trust {Math.round(rel.trust * 100)}%</span>}
                            {rel.dislike > 0.4 && <span className="text-arena-red text-[9px]">Dislike {Math.round(rel.dislike * 100)}%</span>}
                            <span className="text-arena-text-dim text-[9px]">{rel.agrees}A/{rel.disagrees}D</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </Section>
                )}

                {/* Memories */}
                {detail.memories && detail.memories.length > 0 && (
                  <Section title="Memories" icon={<Brain size={12} />}>
                    <div className="space-y-1">
                      {detail.memories.map((mem, i) => (
                        <div key={i} className="text-[10px] text-arena-text-secondary italic">
                          {mem.content}
                        </div>
                      ))}
                    </div>
                  </Section>
                )}

                {/* Recent Conversations */}
                {detail.conversations && detail.conversations.length > 0 && (
                  <Section title="Recent Conversations" icon={<Users size={12} />}>
                    <div className="space-y-2">
                      {detail.conversations.slice(0, 5).map((conv, i) => (
                        <div key={i} className="text-[11px]">
                          <span className="text-arena-text-dim">→ {conv.signal_author}: </span>
                          <span className="text-arena-text-secondary">{conv.content.slice(0, 100)}</span>
                        </div>
                      ))}
                    </div>
                  </Section>
                )}
              </div>
            )}
      </div>
    </div>
  );
}

function Section({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="card-base p-3">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-arena-purple">{icon}</span>
        <span className="text-[10px] font-semibold text-arena-purple tracking-wider">{title.toUpperCase()}</span>
      </div>
      {children}
    </div>
  );
}

function Stat({ label, value, positive, negative }: { label: string; value: string | number; positive?: boolean; negative?: boolean }) {
  const color = positive ? 'text-arena-green' : negative ? 'text-arena-red' : 'text-white';
  return (
    <div className="flex items-center justify-between">
      <span className="text-arena-text-dim text-[10px]">{label}</span>
      <span className={`font-mono font-semibold ${color}`}>{value}</span>
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
