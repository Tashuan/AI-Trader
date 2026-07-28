import { Target, TrendingUp, AlertTriangle, CheckCircle, Pause } from 'lucide-react';
import type { GoalData } from '../types';

interface GoalProgressProps {
  goalData: GoalData | null;
}

export function GoalProgress({ goalData }: GoalProgressProps) {
  if (!goalData || !goalData.goal) {
    return (
      <div className="card-base p-3">
        <div className="flex items-center gap-2 mb-2">
          <Target size={12} className="text-arena-purple" />
          <span className="text-[10px] font-semibold text-arena-purple tracking-wider">GOAL RUNNER</span>
        </div>
        <p className="text-[11px] text-arena-text-dim">No goal set</p>
      </div>
    );
  }

  const { goal, status, progress_pct, current_equity, starting_equity, can_trade } = goalData;
  const target = goal.target_amount;
  const progress = Math.max(0, Math.min(100, progress_pct));

  const statusConfig: Record<string, { color: string; icon: React.ReactNode; label: string }> = {
    active: { color: 'text-arena-blue', icon: <TrendingUp size={10} />, label: 'ACTIVE' },
    achieved: { color: 'text-arena-green', icon: <CheckCircle size={10} />, label: 'ACHIEVED' },
    max_loss_hit: { color: 'text-arena-red', icon: <AlertTriangle size={10} />, label: 'MAX LOSS HIT' },
    paused: { color: 'text-arena-text-dim', icon: <Pause size={10} />, label: 'PAUSED' },
    no_goal: { color: 'text-arena-text-dim', icon: <Target size={10} />, label: 'NO GOAL' },
  };

  const cfg = statusConfig[status] || statusConfig.active;

  return (
    <div className="card-base p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <Target size={12} className="text-arena-purple" />
          <span className="text-[10px] font-semibold text-arena-purple tracking-wider">GOAL RUNNER</span>
        </div>
        <div className={`flex items-center gap-1 ${cfg.color}`}>
          {cfg.icon}
          <span className="text-[9px] font-bold">{cfg.label}</span>
        </div>
      </div>

      {/* Progress bar */}
      <div className="mb-2">
        <div className="flex items-center justify-between text-[10px] mb-1">
          <span className="text-arena-text-dim">Progress</span>
          <span className="font-mono font-semibold text-white">{progress.toFixed(1)}%</span>
        </div>
        <div className="h-2 bg-arena-border rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${
              status === 'achieved' ? 'bg-arena-green' : status === 'max_loss_hit' ? 'bg-arena-red' : 'bg-arena-blue'
            }`}
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>

      {/* Stats grid */}
      <div className="grid grid-cols-2 gap-2 text-[11px]">
        <div className="flex items-center justify-between">
          <span className="text-arena-text-dim text-[10px]">Target</span>
          <span className="font-mono font-semibold text-white">${target.toFixed(0)}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-arena-text-dim text-[10px]">Current</span>
          <span className="font-mono font-semibold text-white">${current_equity.toFixed(0)}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-arena-text-dim text-[10px]">Start</span>
          <span className="font-mono font-semibold text-arena-text-dim">${starting_equity.toFixed(0)}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-arena-text-dim text-[10px]">Can Trade</span>
          <span className={`font-mono font-semibold ${can_trade ? 'text-arena-green' : 'text-arena-red'}`}>
            {can_trade ? 'YES' : 'NO'}
          </span>
        </div>
      </div>

      {/* Max loss badge */}
      {goal.max_loss && (
        <div className="mt-2 flex items-center gap-1">
          <AlertTriangle size={9} className="text-arena-red" />
          <span className="text-[9px] text-arena-text-dim">
            Max loss: ${goal.max_loss.toFixed(0)}
          </span>
        </div>
      )}

      {/* Description */}
      {goal.description && (
        <p className="mt-2 text-[10px] text-arena-text-secondary italic">{goal.description}</p>
      )}
    </div>
  );
}
