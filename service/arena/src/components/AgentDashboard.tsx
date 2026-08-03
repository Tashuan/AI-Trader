import { useEffect, useState, useRef } from 'react';
import {
  TrendingUp, TrendingDown, Brain, Users, Target, Settings,
  ChevronDown, Wifi, WifiOff, Bot, Circle, Activity, DollarSign,
  Zap, Award,
} from 'lucide-react';
import type { Agent, GoalData } from '../types';
import { GrowthChart } from './GrowthChart';
import { PositionTracker } from './PositionTracker';
import { GoalProgress } from './GoalProgress';
import { GoalSetter } from './GoalSetter';
import { StrategySettings } from './StrategySettings';

interface AgentDashboardProps {
  agents: Agent[];
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
  goal_data?: GoalData;
}

const DEFAULT_AGENT = 'BlitzTrader';
const STORAGE_KEY = 'arena:selectedAgent';

export function AgentDashboard({ agents }: AgentDashboardProps) {
  const [selectedAgentName, setSelectedAgentName] = useState(
    () => localStorage.getItem(STORAGE_KEY) || DEFAULT_AGENT
  );
  const [detail, setDetail] = useState<AgentDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [showGoalSetter, setShowGoalSetter] = useState(false);
  const [showStrategySettings, setShowStrategySettings] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Persist selected agent to localStorage
  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, selectedAgentName);
  }, [selectedAgentName]);

  const selectedAgent = agents.find(a => a.name === selectedAgentName) || null;
  const agentId = selectedAgent?.agent_id;

  // Fetch agent detail
  useEffect(() => {
    if (!agentId) return;
    let firstLoad = true;
    const fetchDetail = () => {
      fetch(`/api/arena/agent/${agentId}/detail`)
        .then(r => r.json())
        .then(data => {
          setDetail(data);
          if (firstLoad) { setLoading(false); firstLoad = false; }
        })
        .catch(() => { if (firstLoad) setLoading(false); });
    };
    setLoading(true);
    fetchDetail();
    const interval = setInterval(fetchDetail, 15000);
    return () => clearInterval(interval);
  }, [agentId]);

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  if (agents.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-sm text-arena-text-dim">No agents available</div>
      </div>
    );
  }

  const personality = detail?.personality;
  const stats = detail?.stats;
  const state = detail?.state;
  const positions = detail?.positions || [];
  const reasoning = detail?.reasoning || [];
  const profitHistory = detail?.profit_history || [];
  const conversations = detail?.conversations || [];
  const trades = detail?.trades || [];

  // Compute totals
  const totalUnrealizedPnl = positions.reduce((sum, p) => {
    if (!p.current_price || !p.entry_price) return sum;
    const pnl = p.side === 'long'
      ? (p.current_price - p.entry_price) * Math.abs(p.quantity)
      : (p.entry_price - p.current_price) * Math.abs(p.quantity);
    return sum + pnl;
  }, 0);

  const totalEquity = (stats?.total_profit ?? 0) + 100000;
  const cash = selectedAgent?.today_pnl != null ? totalEquity - totalUnrealizedPnl : totalEquity;

  return (
    <div className="flex-1 overflow-y-auto">
      {/* Header bar with agent selector */}
      <div className="sticky top-0 z-20 bg-arena-card/95 backdrop-blur-sm border-b border-arena-border px-4 py-2.5">
        <div className="flex items-center justify-between gap-3">
          {/* Agent selector dropdown */}
          <div className="relative" ref={dropdownRef}>
            <button
              onClick={() => setDropdownOpen(o => !o)}
              className="flex items-center gap-2.5 px-3 py-1.5 rounded-lg bg-arena-bg border border-arena-border hover:border-arena-border-hover transition-colors"
            >
              <AgentAvatar name={selectedAgentName} size={24} />
              <div className="flex flex-col items-start">
                <span className="text-sm font-bold text-white leading-tight">{selectedAgentName}</span>
                <span className="text-[9px] text-arena-text-dim leading-tight">{personality?.tagline || selectedAgent?.tagline || ''}</span>
              </div>
              <ChevronDown size={14} className={`text-arena-text-dim transition-transform ${dropdownOpen ? 'rotate-180' : ''}`} />
            </button>
            {dropdownOpen && (
              <div className="absolute top-full left-0 mt-1 z-30 min-w-[200px] card-base p-1 shadow-xl max-h-[400px] overflow-y-auto">
                {agents.map(a => (
                  <button
                    key={a.agent_id}
                    onClick={() => { setSelectedAgentName(a.name); setDropdownOpen(false); }}
                    className={`w-full flex items-center gap-2 px-2 py-2 rounded-lg text-left transition-colors ${
                      a.name === selectedAgentName ? 'bg-arena-purple/15' : 'hover:bg-white/5'
                    }`}
                  >
                    <AgentAvatar name={a.name} size={20} />
                    <div className="flex flex-col items-start min-w-0">
                      <span className="text-[11px] font-semibold text-white truncate">{a.name}</span>
                      <span className="text-[9px] text-arena-text-dim truncate">{a.tagline}</span>
                    </div>
                    {a.online && <Wifi size={10} className="text-arena-blue shrink-0 ml-auto" />}
                    {a.bot_running && <Bot size={10} className="text-arena-green shrink-0" />}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Quick stats */}
          <div className="flex items-center gap-4 text-[11px]">
            <QuickStat
              label="Equity"
              value={`$${totalEquity.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`}
              icon={<DollarSign size={12} />}
            />
            <QuickStat
              label="Unrealized"
              value={`${totalUnrealizedPnl >= 0 ? '+' : ''}$${Math.abs(totalUnrealizedPnl).toFixed(2)}`}
              positive={totalUnrealizedPnl >= 0}
              icon={<TrendingUp size={12} />}
            />
            <QuickStat
              label="Total P&L"
              value={`${(stats?.total_profit ?? 0) >= 0 ? '+' : ''}$${Math.abs(stats?.total_profit ?? 0).toFixed(0)}`}
              positive={(stats?.total_profit ?? 0) >= 0}
              icon={<Award size={12} />}
            />
            <QuickStat
              label="Win Rate"
              value={`${Math.round((stats?.win_rate ?? 0) * 100)}%`}
              icon={<Target size={12} />}
            />
          </div>

          {/* Action buttons */}
          <div className="flex items-center gap-1.5">
            <button
              onClick={() => setShowGoalSetter(true)}
              className="p-1.5 rounded-lg bg-arena-purple/10 text-arena-purple hover:bg-arena-purple/20 transition-colors"
              title="Set Goal"
            >
              <Target size={14} />
            </button>
            <button
              onClick={() => setShowStrategySettings(true)}
              className="p-1.5 rounded-lg bg-arena-purple/10 text-arena-purple hover:bg-arena-purple/20 transition-colors"
              title="Strategy Settings"
            >
              <Settings size={14} />
            </button>
          </div>
        </div>
      </div>

      {loading && !detail && (
        <div className="p-8 text-center text-arena-text-dim text-sm">Loading agent details...</div>
      )}

      {detail && (
        <div className="p-4 space-y-4">
          {/* Top row: State + Profile + Goal */}
          <div className="grid grid-cols-3 gap-3">
            {/* Current State */}
            <DashboardCard title="Current State" icon={<Activity size={12} />}>
              <div className="flex items-center gap-2 mb-2">
                <span
                  className="text-[11px] font-mono font-bold px-2 py-0.5 rounded"
                  style={{ color: selectedAgent?.state_color || '#8B92A5', background: (selectedAgent?.state_color || '#8B92A5') + '15' }}
                >
                  {(state?.state || selectedAgent?.state || 'idle').toUpperCase()}
                </span>
                {state?.symbol && (
                  <span className="text-[11px] font-mono text-arena-blue">{state.symbol}</span>
                )}
                {selectedAgent?.online && (
                  <span className="text-[9px] text-arena-blue flex items-center gap-0.5">
                    <Wifi size={8} /> ONLINE
                  </span>
                )}
              </div>
              <p className="text-[11px] text-arena-text-secondary leading-relaxed">
                {state?.detail || selectedAgent?.state_detail || 'No active state'}
              </p>
              {selectedAgent?.confidence != null && (
                <div className="mt-2">
                  <div className="flex items-center justify-between text-[9px] mb-0.5">
                    <span className="text-arena-text-dim">Confidence</span>
                    <span className="font-mono text-white">{Math.round(selectedAgent.confidence * 100)}%</span>
                  </div>
                  <div className="h-1.5 bg-arena-bg rounded-full overflow-hidden">
                    <div
                      className="h-full bg-arena-purple rounded-full transition-all"
                      style={{ width: `${selectedAgent.confidence * 100}%` }}
                    />
                  </div>
                </div>
              )}
            </DashboardCard>

            {/* Profile */}
            <DashboardCard title="Profile" icon={<Brain size={12} />}>
              <p className="text-[11px] text-arena-text-secondary leading-relaxed line-clamp-4">
                {personality?.bio || selectedAgent?.bio || 'No bio available'}
              </p>
              {personality?.goal && (
                <div className="mt-2 flex items-center gap-1.5">
                  <Target size={10} className="text-arena-purple shrink-0" />
                  <span className="text-[10px] text-arena-purple line-clamp-2">{personality.goal}</span>
                </div>
              )}
              {personality?.watchlist && personality.watchlist.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {personality.watchlist.slice(0, 6).map(sym => (
                    <span key={sym} className="text-[9px] font-mono px-1.5 py-0.5 bg-arena-bg rounded text-arena-text-secondary">
                      {sym}
                    </span>
                  ))}
                </div>
              )}
            </DashboardCard>

            {/* Goal Runner */}
            <GoalProgress goalData={detail.goal_data || selectedAgent?.goal_data || null} />
          </div>

          {/* Performance Stats — full width strip */}
          <DashboardCard title="Performance Stats" icon={<TrendingUp size={12} />}>
            <div className="grid grid-cols-7 gap-3">
              <StatBox label="Total Trades" value={stats?.total_trades ?? 0} />
              <StatBox label="Win Rate" value={`${Math.round((stats?.win_rate ?? 0) * 100)}%`} />
              <StatBox
                label="Current Streak"
                value={(stats?.current_streak ?? 0) > 0 ? `W${stats?.current_streak}` : (stats?.current_streak ?? 0) < 0 ? `L${Math.abs(stats?.current_streak ?? 0)}` : '0'}
                positive={(stats?.current_streak ?? 0) > 0}
                negative={(stats?.current_streak ?? 0) < 0}
              />
              <StatBox label="Best Streak" value={stats?.best_streak ?? 0} positive />
              <StatBox
                label="Total P&L"
                value={`${(stats?.total_profit ?? 0) >= 0 ? '+' : '-'}$${Math.abs(stats?.total_profit ?? 0).toFixed(0)}`}
                positive={(stats?.total_profit ?? 0) >= 0}
              />
              <StatBox
                label="Max Drawdown"
                value={`$${Math.abs(stats?.max_drawdown ?? 0).toFixed(0)}`}
                negative
              />
              <StatBox
                label="Winning Trades"
                value={stats?.winning_trades ?? 0}
              />
            </div>
          </DashboardCard>

          {/* Positions + Growth Chart side by side */}
          <div className="grid grid-cols-2 gap-3">
            {/* Open Positions */}
            <DashboardCard title="Open Positions" icon={<TrendingUp size={12} />} badge={positions.length}>
              {positions.length > 0 ? (
                <div className="space-y-2">
                  {positions.map((pos, i) => {
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
              ) : (
                <EmptyState text="No open positions" />
              )}
            </DashboardCard>

            {/* Growth Chart */}
            <DashboardCard title="Growth" icon={<TrendingUp size={12} />}>
              {profitHistory.length > 1 ? (
                <GrowthChart data={profitHistory} height={220} />
              ) : (
                <EmptyState text="Not enough data" />
              )}
            </DashboardCard>
          </div>

          {/* Recent Trades + Reasoning */}
          <div className="grid grid-cols-2 gap-3">
            {/* Recent Trades */}
            <DashboardCard title="Recent Trades" icon={<Zap size={12} />} badge={trades.length}>
              {trades.length > 0 ? (
                <div className="space-y-1.5 max-h-[280px] overflow-y-auto">
                  {trades.slice(0, 12).map((t, i) => (
                    <div key={i} className="flex items-center justify-between text-[11px] py-1.5 px-2 rounded bg-arena-bg/50">
                      <div className="flex items-center gap-2">
                        <span className={`text-[9px] font-mono font-bold ${t.side === 'long' ? 'text-arena-green' : 'text-arena-red'}`}>
                          {(t.side || '—').toUpperCase()}
                        </span>
                        <span className="font-mono text-white">{t.symbol}</span>
                        <span className="text-[9px] text-arena-text-dim">{t.signal_type}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        {t.pnl != null && (
                          <span className={`font-mono font-semibold ${t.pnl >= 0 ? 'text-arena-green' : 'text-arena-red'}`}>
                            {t.pnl >= 0 ? '+' : ''}${Math.abs(t.pnl).toFixed(2)}
                          </span>
                        )}
                        <span className="text-[9px] text-arena-text-dim">{formatDate(t.created_at)}</span>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyState text="No recent trades" />
              )}
            </DashboardCard>

            {/* Recent Analysis */}
            <DashboardCard title="Recent Analysis" icon={<Brain size={12} />} badge={reasoning.length}>
              {reasoning.length > 0 ? (
                <div className="space-y-2 max-h-[280px] overflow-y-auto">
                  {reasoning.slice(0, 6).map((r, i) => (
                    <div key={i} className="text-[11px] p-2 rounded bg-arena-bg/50">
                      <div className="text-white font-medium text-[11px]">{r.title}</div>
                      <div className="text-arena-text-secondary line-clamp-3 mt-0.5 text-[10px]">{r.content}</div>
                      <div className="text-[9px] text-arena-text-dim mt-0.5">{formatDate(r.created_at)}</div>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyState text="No analysis published" />
              )}
            </DashboardCard>
          </div>

          {/* Conversations — full width */}
          <DashboardCard title="Recent Conversations" icon={<Users size={12} />} badge={conversations.length}>
            {conversations.length > 0 ? (
              <div className="grid grid-cols-2 gap-2 max-h-[300px] overflow-y-auto">
                {conversations.slice(0, 10).map((conv, i) => (
                  <div key={i} className="text-[11px] p-2 rounded bg-arena-bg/30">
                    <div className="flex items-center gap-1.5 mb-0.5">
                      <span className="text-[9px] font-semibold text-arena-blue">→ {conv.signal_author}</span>
                      <span className="text-[9px] text-arena-text-dim">{conv.signal_title}</span>
                    </div>
                    <span className="text-arena-text-secondary text-[10px]">{conv.content.slice(0, 200)}</span>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState text="No conversations" />
            )}
          </DashboardCard>

          {/* Thoughts feed — full width */}
          {selectedAgent?.thoughts && selectedAgent.thoughts.length > 0 && (
            <DashboardCard title="Live Thoughts" icon={<Brain size={12} />} badge={selectedAgent.thoughts.length}>
              <div className="space-y-1.5">
                {selectedAgent.thoughts.map((thought, i) => (
                  <div key={i} className="text-[11px] text-arena-text-secondary italic p-2 rounded bg-arena-bg/30 border-l-2 border-arena-purple/40">
                    {thought}
                  </div>
                ))}
              </div>
            </DashboardCard>
          )}
        </div>
      )}

      {/* Modals */}
      {showGoalSetter && selectedAgent && (
        <GoalSetter
          agentId={selectedAgent.agent_id}
          goalData={detail?.goal_data || selectedAgent?.goal_data || null}
          onClose={() => setShowGoalSetter(false)}
          onUpdated={() => {
            if (selectedAgent) {
              fetch(`/api/arena/agent/${selectedAgent.agent_id}/detail`)
                .then(r => r.json())
                .then(data => setDetail(data))
                .catch(() => {});
            }
          }}
        />
      )}

      {showStrategySettings && selectedAgent && (
        <StrategySettings
          agentId={selectedAgent.agent_id}
          onClose={() => setShowStrategySettings(false)}
        />
      )}
    </div>
  );
}

// ── Helper Components ──

function DashboardCard({ title, icon, badge, children }: { title: string; icon: React.ReactNode; badge?: number; children: React.ReactNode }) {
  return (
    <div className="card-base p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-arena-purple">{icon}</span>
          <span className="text-[10px] font-semibold text-arena-purple tracking-wider">{title.toUpperCase()}</span>
        </div>
        {badge != null && badge > 0 && (
          <span className="text-[9px] font-mono text-arena-text-dim bg-arena-bg px-1.5 py-0.5 rounded">
            {badge}
          </span>
        )}
      </div>
      {children}
    </div>
  );
}

function QuickStat({ label, value, icon, positive, negative }: { label: string; value: string; icon: React.ReactNode; positive?: boolean; negative?: boolean }) {
  const color = positive ? 'text-arena-green' : negative ? 'text-arena-red' : 'text-white';
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-arena-text-dim">{icon}</span>
      <div className="flex flex-col">
        <span className="text-[9px] text-arena-text-dim leading-tight">{label}</span>
        <span className={`font-mono font-semibold ${color} leading-tight`}>{value}</span>
      </div>
    </div>
  );
}

function StatBox({ label, value, positive, negative }: { label: string; value: string | number; positive?: boolean; negative?: boolean }) {
  const color = positive ? 'text-arena-green' : negative ? 'text-arena-red' : 'text-white';
  return (
    <div className="text-center">
      <div className="text-[9px] text-arena-text-dim mb-0.5">{label}</div>
      <div className={`text-sm font-mono font-bold ${color}`}>{value}</div>
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="flex items-center justify-center py-6 text-[11px] text-arena-text-dim">
      {text}
    </div>
  );
}

function AgentAvatar({ name, size = 32 }: { name: string; size?: number }) {
  const seed = name.toLowerCase().replace(/[^a-z0-9]/g, '');
  const hue = seed.charCodeAt(0) * 37 % 360;
  const initials = name.split(/(?=[A-Z])/).map(w => w[0]).join('').slice(0, 2).toUpperCase();
  return (
    <div
      className="rounded-lg flex items-center justify-center font-bold text-white shrink-0"
      style={{
        width: size,
        height: size,
        fontSize: size * 0.32,
        background: `linear-gradient(135deg, hsl(${hue}, 60%, 30%), hsl(${(hue + 40) % 360}, 60%, 20%))`,
        border: '1px solid rgba(255,255,255,.08)',
      }}
    >
      {initials}
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
