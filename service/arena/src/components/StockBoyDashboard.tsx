import { useMemo, useState } from 'react';
import {
  Activity, AlertTriangle, Bot, CheckCircle2, Clock3, Eye, Pause, Play,
  RefreshCw, Shield, ShieldAlert, SlidersHorizontal, Square, Target, Zap,
} from 'lucide-react';
import { useStockBoyData } from '../hooks/useStockBoyData';
import type { StockBoyAction, StockBoyPosition, StockBoyRunnerHealth } from '../types';

export function StockBoyDashboard({ onBack }: { onBack?: () => void }) {
  const { snapshot, loading, error, lastUpdated, refresh, runControl } = useStockBoyData();
  const [controlLoading, setControlLoading] = useState(false);

  const run = async (path: string, body: Record<string, unknown> = {}) => {
    setControlLoading(true);
    await runControl(path, body);
    setControlLoading(false);
  };

  if (loading && !snapshot) return <EmptyState message="Loading StockBoy overwatch…" loading />;
  if (error && !snapshot) return <EmptyState message={error} />;
  if (!snapshot) return <EmptyState message="StockBoy snapshot is unavailable" />;

  const { supervisor, portfolio } = snapshot;
  const stale = !portfolio.data_fresh || Boolean(error);
  const statusLabel = supervisor.kill_switch ? 'KILL SWITCH' : supervisor.running ? 'OVERWATCH ACTIVE' : 'STANDBY';

  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-4">
      <header className="card-base p-4 border-l-2 border-arena-purple">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-arena-purple/15 flex items-center justify-center">
              <Eye size={21} className="text-arena-purple" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-lg font-bold text-white">StockBoy</h1>
                <span className="text-[9px] font-mono font-bold px-1.5 py-0.5 rounded bg-arena-purple/15 text-arena-purple">SUPERVISOR</span>
                <span className="text-[9px] font-mono font-bold px-1.5 py-0.5 rounded bg-arena-blue/15 text-arena-blue">PAPER ONLY</span>
              </div>
              <p className="text-[10px] text-arena-text-dim">Platform overwatch and position maintenance brain</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {onBack && <button onClick={onBack} className="text-[10px] text-arena-text-secondary hover:text-white px-2 py-1 rounded-lg border border-arena-border">Back to agents</button>}
            <StatusPill label={statusLabel} tone={supervisor.kill_switch ? 'red' : supervisor.running ? 'green' : 'yellow'} />
            <StatusPill label={stale ? 'STALE DATA' : 'DATA CURRENT'} tone={stale ? 'yellow' : 'green'} />
            <button onClick={() => refresh()} className="p-2 rounded-lg bg-arena-bg text-arena-text-secondary hover:text-white" title="Refresh">
              <RefreshCw size={14} />
            </button>
          </div>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4 text-[10px]">
          <Meta label="Last cycle" value={formatTime(supervisor.last_cycle_at)} />
          <Meta label="Next cycle" value={formatTime(supervisor.next_cycle_at)} />
          <Meta label="Cycles" value={String(supervisor.cycles_run)} />
          <Meta label="Updated" value={lastUpdated ? formatTime(new Date(lastUpdated).toISOString()) : '—'} />
        </div>
        {supervisor.last_error && <div className="mt-3 text-[10px] text-arena-red flex items-center gap-1.5"><AlertTriangle size={12} />{supervisor.last_error}</div>}
      </header>

      <section className="grid grid-cols-2 lg:grid-cols-6 gap-2">
        <Metric label="Paper equity" value={money(portfolio.total_equity)} icon={<Target size={12} />} />
        <Metric label="Cash" value={money(portfolio.total_cash)} icon={<Shield size={12} />} />
        <Metric label="Unrealized P&L" value={money(portfolio.total_unrealized_pnl)} positive={portfolio.total_unrealized_pnl >= 0} icon={<Activity size={12} />} />
        <Metric label="Gross exposure" value={money(portfolio.gross_exposure)} icon={<Zap size={12} />} />
        <Metric label="Open positions" value={String(portfolio.open_position_count)} icon={<Target size={12} />} />
        <Metric label="Pending orders" value={String(portfolio.pending_order_count)} icon={<Clock3 size={12} />} />
      </section>

      <section>
        <SectionTitle icon={<Bot size={14} />} title="Runner overwatch" detail="Controlled deterministic runners" />
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {snapshot.runners.map(runner => <RunnerCard key={runner.runner_key} runner={runner} />)}
        </div>
      </section>

      <section className="grid grid-cols-1 xl:grid-cols-5 gap-3">
        <div className="xl:col-span-3 card-base p-3">
          <SectionTitle icon={<Target size={14} />} title="Position watchtower" detail={`${snapshot.positions.length} controlled positions`} />
          <div className="space-y-2">
            {snapshot.positions.length === 0 ? <EmptyInline message="No controlled positions open" /> : snapshot.positions.map(position => <PositionRow key={position.position_id} position={position} />)}
          </div>
        </div>
        <div className="xl:col-span-2 card-base p-3">
          <SectionTitle icon={<ShieldAlert size={14} />} title="Risk and anomalies" detail={`${snapshot.risk_anomalies.length} findings`} />
          <div className="space-y-2">
            {snapshot.risk_anomalies.length === 0 ? <EmptyInline message="No active anomalies" success /> : snapshot.risk_anomalies.map((item, index) => (
              <div key={`${item.category}-${index}`} className="rounded-lg bg-arena-bg p-2.5 flex gap-2">
                <AlertTriangle size={13} className={item.severity === 'critical' ? 'text-arena-red' : 'text-arena-yellow'} />
                <div><div className="text-[10px] text-white font-semibold">{item.category.replace(/_/g, ' ')}</div><div className="text-[10px] text-arena-text-secondary">{item.message}</div></div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <ActivityPanel title="Action and maintenance queue" icon={<SlidersHorizontal size={14} />}>
          {snapshot.recent_actions.length === 0 ? <EmptyInline message="No StockBoy actions recorded" /> : snapshot.recent_actions.slice(0, 8).map(action => <ActionRow key={action.action_id} action={action} />)}
        </ActivityPanel>
        <ActivityPanel title="Commentary and journal" icon={<Eye size={14} />}>
          {snapshot.recent_commentary.length === 0 ? <EmptyInline message="No commentary recorded" /> : snapshot.recent_commentary.slice(0, 8).map(item => <div key={item.commentary_id} className="border-b border-arena-border/50 py-2 last:border-0"><div className="text-[10px] text-white">{item.content}</div><div className="text-[9px] text-arena-text-dim mt-1">{formatTime(item.created_at)} · {item.severity}</div></div>)}
        </ActivityPanel>
      </section>

      <section className="card-base p-3">
        <SectionTitle icon={<Shield size={14} />} title="Supervisor controls" detail="Stopping StockBoy does not stop runners or close positions" />
        <div className="flex flex-wrap gap-2">
          <ControlButton disabled={controlLoading} icon={<Play size={12} />} label="Start StockBoy" onClick={() => run('/arena/stockboy/start')} />
          <ControlButton disabled={controlLoading} icon={<Square size={12} />} label="Stop StockBoy" onClick={() => run('/arena/stockboy/stop')} />
          <ControlButton disabled={controlLoading} icon={<Pause size={12} />} label={supervisor.actions_enabled ? 'Disable actions' : 'Enable actions'} onClick={() => run('/stockboy/enable', { enabled: !supervisor.actions_enabled })} />
          <ControlButton danger disabled={controlLoading} icon={<ShieldAlert size={12} />} label={supervisor.kill_switch ? 'Release kill switch' : 'Engage kill switch'} onClick={() => run('/stockboy/kill-switch', { engaged: !supervisor.kill_switch, reason: 'Dashboard operator control' })} />
        </div>
      </section>
    </div>
  );
}

function RunnerCard({ runner }: { runner: StockBoyRunnerHealth }) {
  const healthy = runner.running && !runner.last_error;
  return <div className="card-base p-3"><div className="flex justify-between items-start"><div><div className="text-xs font-bold text-white">{runner.agent_name}</div><div className="text-[9px] text-arena-text-dim">{runner.runner_key} · {runner.open_positions} positions</div></div><StatusPill label={runner.last_error ? 'ERROR' : runner.running ? 'RUNNING' : 'STOPPED'} tone={runner.last_error ? 'red' : healthy ? 'green' : 'yellow'} /></div><div className="grid grid-cols-2 gap-2 mt-3"><MiniMetric label="Portfolio" value={money(runner.portfolio_value)} /><MiniMetric label="Unrealized" value={money(runner.unrealized_pnl)} /><MiniMetric label="Heartbeat" value={runner.heartbeat_age_seconds == null ? '—' : `${Math.round(runner.heartbeat_age_seconds)}s`} /><MiniMetric label="Overrides" value={String(runner.active_overrides)} /></div>{runner.last_error && <div className="mt-2 text-[9px] text-arena-red truncate">{runner.last_error}</div>}</div>;
}

function PositionRow({ position }: { position: StockBoyPosition }) {
  const pnl = position.unrealized_pnl ?? 0;
  return <div className="rounded-lg bg-arena-bg p-2.5"><div className="flex justify-between gap-2"><div className="flex items-center gap-2"><span className="text-[10px] font-bold text-white">{position.symbol}</span><span className="text-[9px] text-arena-text-dim">{position.agent_name}</span><span className={`text-[9px] font-mono ${position.side === 'long' ? 'text-arena-green' : 'text-arena-red'}`}>{position.side.toUpperCase()}</span></div><span className={`text-[10px] font-mono font-semibold ${pnl >= 0 ? 'text-arena-green' : 'text-arena-red'}`}>{money(pnl)} ({(position.unrealized_pnl_pct ?? 0).toFixed(2)}%)</span></div><div className="grid grid-cols-2 md:grid-cols-5 gap-2 mt-2 text-[9px]"><MiniMetric label="Qty" value={String(position.quantity)} /><MiniMetric label="Entry" value={money(position.entry_price)} /><MiniMetric label="Current" value={position.current_price == null ? 'STALE' : money(position.current_price)} /><MiniMetric label="Stop" value={position.stop_loss_price == null ? 'Missing' : money(position.stop_loss_price)} /><MiniMetric label="Target" value={position.take_profit_price == null ? 'Missing' : money(position.take_profit_price)} /></div>{(position.missing_protection || position.stale_price) && <div className="mt-2 text-[9px] text-arena-yellow flex items-center gap-1"><AlertTriangle size={10} />{position.missing_protection ? 'Missing protection' : 'Stale current price'}</div>}</div>;
}

function ActionRow({ action }: { action: StockBoyAction }) { return <div className="border-b border-arena-border/50 py-2 last:border-0 flex items-start gap-2"><span className={action.status === 'executed' ? 'text-arena-green' : action.status === 'failed' || action.status === 'rejected' ? 'text-arena-red' : 'text-arena-yellow'}>{action.status === 'executed' ? <CheckCircle2 size={12} /> : <AlertTriangle size={12} />}</span><div className="min-w-0"><div className="text-[10px] text-white">{action.runner_key} · {action.action_type} · {action.status}</div><div className="text-[9px] text-arena-text-secondary truncate">{action.rationale || action.error || 'No rationale'}</div><div className="text-[9px] text-arena-text-dim">{formatTime(action.created_at)}</div></div></div>; }
function ActivityPanel({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) { return <div className="card-base p-3"><SectionTitle icon={icon} title={title} />{children}</div>; }
function SectionTitle({ icon, title, detail }: { icon: React.ReactNode; title: string; detail?: string }) { return <div className="flex items-center gap-2 mb-3"><span className="text-arena-purple">{icon}</span><span className="text-xs font-bold text-white">{title}</span>{detail && <span className="text-[9px] text-arena-text-dim">{detail}</span>}</div>; }
function Metric({ label, value, icon, positive }: { label: string; value: string; icon: React.ReactNode; positive?: boolean }) { return <div className="card-base p-2.5"><div className="flex items-center gap-1 text-[9px] text-arena-text-dim">{icon}{label}</div><div className={`font-mono text-sm font-semibold mt-1 ${positive == null ? 'text-white' : positive ? 'text-arena-green' : 'text-arena-red'}`}>{value}</div></div>; }
function MiniMetric({ label, value }: { label: string; value: string }) { return <div><div className="text-arena-text-dim">{label}</div><div className="font-mono text-white truncate">{value}</div></div>; }
function Meta({ label, value }: { label: string; value: string }) { return <div><div className="text-arena-text-dim">{label}</div><div className="font-mono text-white mt-0.5">{value}</div></div>; }
function StatusPill({ label, tone }: { label: string; tone: 'green' | 'yellow' | 'red' }) { const colors = { green: 'text-arena-green bg-arena-green/10', yellow: 'text-arena-yellow bg-arena-yellow/10', red: 'text-arena-red bg-arena-red/10' }; return <span className={`text-[9px] font-mono font-bold px-1.5 py-0.5 rounded ${colors[tone]}`}>{label}</span>; }
function ControlButton({ label, icon, onClick, disabled, danger }: { label: string; icon: React.ReactNode; onClick: () => void; disabled: boolean; danger?: boolean }) { return <button disabled={disabled} onClick={() => { if (window.confirm(`${label}?`)) onClick(); }} className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[10px] border transition-colors disabled:opacity-50 ${danger ? 'border-arena-red/30 text-arena-red hover:bg-arena-red/10' : 'border-arena-border text-arena-text-secondary hover:text-white hover:bg-white/5'}`}>{icon}{label}</button>; }
function EmptyInline({ message, success }: { message: string; success?: boolean }) { return <div className="py-5 text-center text-[10px] text-arena-text-dim flex items-center justify-center gap-1.5">{success && <CheckCircle2 size={12} className="text-arena-green" />}{message}</div>; }
function EmptyState({ message, loading: isLoading }: { message: string; loading?: boolean }) { return <div className="flex-1 flex items-center justify-center p-8"><div className="text-center text-sm text-arena-text-dim">{isLoading && <RefreshCw size={18} className="mx-auto mb-2 animate-spin text-arena-purple" />}{message}</div></div>; }
function money(value: number) { return `${value < 0 ? '-' : ''}$${Math.abs(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`; }
function formatTime(value?: string | null) { if (!value) return '—'; const date = new Date(value); return Number.isNaN(date.getTime()) ? '—' : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); }
