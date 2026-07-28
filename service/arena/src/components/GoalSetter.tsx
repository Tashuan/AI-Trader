import { useState } from 'react';
import { Target, X, Trash2 } from 'lucide-react';
import type { GoalData } from '../types';

interface GoalSetterProps {
  agentId: number;
  goalData: GoalData | null;
  onClose: () => void;
  onUpdated: () => void;
}

function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const token = localStorage.getItem('auth_token');
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return headers;
}

export function GoalSetter({ agentId, goalData, onClose, onUpdated }: GoalSetterProps) {
  const [targetAmount, setTargetAmount] = useState(
    goalData?.goal?.target_amount?.toString() || ''
  );
  const [maxLoss, setMaxLoss] = useState(
    goalData?.goal?.max_loss?.toString() || ''
  );
  const [deadline, setDeadline] = useState(
    goalData?.goal?.deadline || ''
  );
  const [description, setDescription] = useState(
    goalData?.goal?.description || ''
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSave = async () => {
    const target = parseFloat(targetAmount);
    if (!target || target <= 0) {
      setError('Target amount must be a positive number');
      return;
    }

    setSaving(true);
    setError(null);

    try {
      const res = await fetch(`/api/agents/manage/${agentId}/goal`, {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({
          target_amount: target,
          max_loss: maxLoss ? parseFloat(maxLoss) : null,
          deadline: deadline || null,
          description: description || null,
        }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Failed to set goal');
      }

      onUpdated();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to set goal');
    } finally {
      setSaving(false);
    }
  };

  const handleClear = async () => {
    setSaving(true);
    setError(null);

    try {
      const res = await fetch(`/api/agents/manage/${agentId}/goal`, {
        method: 'DELETE',
        headers: authHeaders(),
      });

      if (!res.ok) {
        throw new Error('Failed to clear goal');
      }

      onUpdated();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to clear goal');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center">
      <div className="absolute inset-0 bg-black/70" onClick={onClose} />

      <div className="relative w-full max-w-sm bg-arena-card border border-arena-border rounded-lg p-4 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Target size={14} className="text-arena-purple" />
            <span className="text-sm font-semibold text-white">Set Trading Goal</span>
          </div>
          <button onClick={onClose} className="text-arena-text-dim hover:text-white transition-colors">
            <X size={16} />
          </button>
        </div>

        {error && (
          <div className="text-[11px] text-arena-red bg-arena-red/10 rounded p-2">
            {error}
          </div>
        )}

        {/* Target amount */}
        <div>
          <label className="text-[10px] text-arena-text-dim mb-1 block">Target Profit ($)</label>
          <input
            type="number"
            value={targetAmount}
            onChange={e => setTargetAmount(e.target.value)}
            placeholder="e.g. 5000"
            className="w-full bg-arena-bg border border-arena-border rounded px-2 py-1.5 text-[12px] text-white focus:outline-none focus:border-arena-purple"
          />
        </div>

        {/* Max loss */}
        <div>
          <label className="text-[10px] text-arena-text-dim mb-1 block">Max Loss ($) — optional</label>
          <input
            type="number"
            value={maxLoss}
            onChange={e => setMaxLoss(e.target.value)}
            placeholder="e.g. 2000"
            className="w-full bg-arena-bg border border-arena-border rounded px-2 py-1.5 text-[12px] text-white focus:outline-none focus:border-arena-purple"
          />
        </div>

        {/* Deadline */}
        <div>
          <label className="text-[10px] text-arena-text-dim mb-1 block">Deadline — optional</label>
          <input
            type="date"
            value={deadline ? deadline.split('T')[0] : ''}
            onChange={e => setDeadline(e.target.value)}
            className="w-full bg-arena-bg border border-arena-border rounded px-2 py-1.5 text-[12px] text-white focus:outline-none focus:border-arena-purple"
          />
        </div>

        {/* Description */}
        <div>
          <label className="text-[10px] text-arena-text-dim mb-1 block">Description — optional</label>
          <input
            type="text"
            value={description}
            onChange={e => setDescription(e.target.value)}
            placeholder="e.g. Make $5K by end of month"
            className="w-full bg-arena-bg border border-arena-border rounded px-2 py-1.5 text-[12px] text-white focus:outline-none focus:border-arena-purple"
          />
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2 pt-2">
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex-1 bg-arena-purple hover:bg-arena-purple/80 disabled:opacity-50 text-white text-[11px] font-semibold py-1.5 rounded transition-colors"
          >
            {saving ? 'Saving...' : 'Set Goal'}
          </button>
          {goalData?.goal && (
            <button
              onClick={handleClear}
              disabled={saving}
              className="flex items-center gap-1 bg-arena-red/20 hover:bg-arena-red/30 disabled:opacity-50 text-arena-red text-[11px] font-semibold py-1.5 px-3 rounded transition-colors"
            >
              <Trash2 size={11} />
              Clear
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
