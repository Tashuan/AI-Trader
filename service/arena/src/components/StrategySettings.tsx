import { useEffect, useState } from 'react';
import { Settings, X, RotateCcw, Plus, Trash2 } from 'lucide-react';

interface StrategyParams {
  exit_rules?: Record<string, any>;
  entry_criteria?: Record<string, any>;
  position_sizing?: Record<string, any>;
  switch_logic?: Record<string, any>;
  scoring_weights?: Record<string, any>;
  indicators?: Record<string, any>;
  watchlist?: string[];
  sweep?: Record<string, any>;
  cycle_timing?: Record<string, any>;
}

interface StrategySettingsProps {
  agentId: number;
  onClose: () => void;
}

function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const token = localStorage.getItem('auth_token');
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return headers;
}

export function StrategySettings({ agentId, onClose }: StrategySettingsProps) {
  const [params, setParams] = useState<StrategyParams>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedSection, setExpandedSection] = useState<string | null>('exit_rules');
  const [watchlistInput, setWatchlistInput] = useState('');

  useEffect(() => {
    fetch(`/api/agents/manage/${agentId}/strategy-params`, { headers: authHeaders() })
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(data => {
        setParams(data.strategy_params || {});
        setLoading(false);
      })
      .catch((e) => {
        setError(e instanceof Error ? e.message : 'Failed to load strategy params');
        setLoading(false);
      });
  }, [agentId]);

  const handleSave = async (section: string, values: Record<string, any>) => {
    setSaving(true);
    setError(null);

    try {
      const res = await fetch(`/api/agents/manage/${agentId}/strategy-params`, {
        method: 'PATCH',
        headers: authHeaders(),
        body: JSON.stringify({ [section]: values }),
      });

      if (!res.ok) throw new Error('Failed to update strategy params');

      const data = await res.json();
      setParams(prev => ({ ...prev, ...data.strategy_params }));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update');
    } finally {
      setSaving(false);
    }
  };

  const handleSaveWatchlist = async () => {
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`/api/agents/manage/${agentId}/strategy-params`, {
        method: 'PATCH',
        headers: authHeaders(),
        body: JSON.stringify({ watchlist: params.watchlist || [] }),
      });
      if (!res.ok) throw new Error('Failed to update watchlist');
      const data = await res.json();
      setParams(prev => ({ ...prev, ...data.strategy_params }));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update');
    } finally {
      setSaving(false);
    }
  };

  const addWatchlistSymbol = () => {
    const sym = watchlistInput.trim().toUpperCase();
    if (sym && !(params.watchlist || []).includes(sym)) {
      setParams(prev => ({ ...prev, watchlist: [...(prev.watchlist || []), sym] }));
    }
    setWatchlistInput('');
  };

  const removeWatchlistSymbol = (sym: string) => {
    setParams(prev => ({ ...prev, watchlist: (prev.watchlist || []).filter(s => s !== sym) }));
  };

  const sections: { key: string; label: string; fields: { key: string; label: string; type: 'number' | 'text' }[] }[] = [
    {
      key: 'exit_rules',
      label: 'Exit Rules',
      fields: [
        { key: 'stop_loss_pct', label: 'Stop Loss %', type: 'number' },
        { key: 'take_profit_pct', label: 'Take Profit %', type: 'number' },
        { key: 'stagnation_cycles', label: 'Stagnation Cycles', type: 'number' },
        { key: 'stagnation_threshold_pct', label: 'Stagnation Threshold %', type: 'number' },
        { key: 'momentum_death_vol_ratio', label: 'Momentum Death Vol Ratio', type: 'number' },
        { key: 'ob_exhaustion_rsi', label: 'OB Exhaustion RSI', type: 'number' },
      ],
    },
    {
      key: 'entry_criteria',
      label: 'Entry Criteria',
      fields: [
        { key: 'min_signals', label: 'Min Signals', type: 'number' },
        { key: 'min_signal_families', label: 'Min Signal Families', type: 'number' },
        { key: 'min_vol_ratio', label: 'Min Vol Ratio', type: 'number' },
        { key: 'bearish_macro_min_signals', label: 'Bearish Macro Min Signals', type: 'number' },
        { key: 'bearish_macro_threshold', label: 'Bearish Macro Threshold', type: 'number' },
      ],
    },
    {
      key: 'position_sizing',
      label: 'Position Sizing',
      fields: [
        { key: 'max_positions', label: 'Max Positions', type: 'number' },
        { key: 'normal_sizing_min_pct', label: 'Normal Sizing Min %', type: 'number' },
        { key: 'normal_sizing_max_pct', label: 'Normal Sizing Max %', type: 'number' },
        { key: 'approaching_sizing_min_pct', label: 'Approaching Sizing Min %', type: 'number' },
        { key: 'approaching_sizing_max_pct', label: 'Approaching Sizing Max %', type: 'number' },
        { key: 'final_stretch_tp_pct', label: 'Final Stretch TP %', type: 'number' },
        { key: 'consecutive_loss_threshold', label: 'Consecutive Loss Threshold', type: 'number' },
        { key: 'consecutive_loss_size_cut_pct', label: 'Consecutive Loss Size Cut %', type: 'number' },
        { key: 'consecutive_loss_min_signals', label: 'Consecutive Loss Min Signals', type: 'number' },
      ],
    },
    {
      key: 'switch_logic',
      label: 'Switch Logic',
      fields: [
        { key: 'switch_score_threshold_pct', label: 'Switch Score Threshold %', type: 'number' },
        { key: 'reentry_cooldown_cycles', label: 'Reentry Cooldown Cycles', type: 'number' },
      ],
    },
    {
      key: 'scoring_weights',
      label: 'Scoring Weights',
      fields: [
        { key: 'signal_count_weight', label: 'Signal Count Weight', type: 'number' },
        { key: 'family_diversity_weight', label: 'Family Diversity Weight', type: 'number' },
        { key: 'candle_quality_weight', label: 'Candle Quality Weight', type: 'number' },
        { key: 'consolidation_bonus_weight', label: 'Consolidation Bonus Weight', type: 'number' },
      ],
    },
    {
      key: 'indicators',
      label: 'Indicators',
      fields: [
        { key: 'rsi_period', label: 'RSI Period', type: 'number' },
        { key: 'rsi_bullish', label: 'RSI Bullish Threshold', type: 'number' },
        { key: 'rsi_overbought', label: 'RSI Overbought', type: 'number' },
        { key: 'rsi_oversold', label: 'RSI Oversold', type: 'number' },
        { key: 'macd_fast', label: 'MACD Fast', type: 'number' },
        { key: 'macd_slow', label: 'MACD Slow', type: 'number' },
        { key: 'macd_signal', label: 'MACD Signal', type: 'number' },
        { key: 'ema_period', label: 'EMA Period', type: 'number' },
        { key: 'stochastic_period', label: 'Stochastic Period', type: 'number' },
        { key: 'atr_period', label: 'ATR Period', type: 'number' },
        { key: 'vol_ratio_bullish', label: 'Vol Ratio Bullish', type: 'number' },
        { key: 'vol_ratio_dead', label: 'Vol Ratio Dead', type: 'number' },
      ],
    },
  ];

  return (
    <div className="fixed inset-0 z-[60] flex justify-end">
      <div className="absolute inset-0 bg-black/70" onClick={onClose} />

      <div className="relative w-full max-w-md bg-arena-card border-l border-arena-border overflow-y-auto">
        {/* Header */}
        <div className="sticky top-0 z-10 bg-arena-card border-b border-arena-border p-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Settings size={14} className="text-arena-purple" />
            <span className="text-sm font-semibold text-white">Strategy Settings</span>
          </div>
          <button onClick={onClose} className="text-arena-text-dim hover:text-white transition-colors">
            <X size={16} />
          </button>
        </div>

        {loading && (
          <div className="p-8 text-center text-arena-text-dim text-sm">Loading strategy params...</div>
        )}

        {error && (
          <div className="m-3 text-[11px] text-arena-red bg-arena-red/10 rounded p-2">
            {error}
          </div>
        )}

        {!loading && (
          <div className="p-3 space-y-2">
            {sections.map(section => {
              const isExpanded = expandedSection === section.key;
              const sectionValues = params[section.key as keyof StrategyParams] as Record<string, any> || {};

              return (
                <div key={section.key} className="card-base">
                  <button
                    onClick={() => setExpandedSection(isExpanded ? null : section.key)}
                    className="w-full flex items-center justify-between p-2.5 text-left"
                  >
                    <span className="text-[11px] font-semibold text-arena-purple tracking-wider">
                      {section.label.toUpperCase()}
                    </span>
                    <span className="text-arena-text-dim text-[10px]">{isExpanded ? '−' : '+'}</span>
                  </button>

                  {isExpanded && (
                    <div className="px-2.5 pb-3 space-y-2">
                      {section.fields.map(field => (
                        <ParamInput
                          key={field.key}
                          label={field.label}
                          value={sectionValues[field.key]}
                          type={field.type}
                          onChange={(val) => {
                            setParams(prev => ({
                              ...prev,
                              [section.key]: { ...prev[section.key as keyof StrategyParams] as Record<string, any>, [field.key]: val },
                            }));
                          }}
                        />
                      ))}
                      <button
                        onClick={() => handleSave(section.key, params[section.key as keyof StrategyParams] as Record<string, any> || {})}
                        disabled={saving}
                        className="w-full bg-arena-purple/20 hover:bg-arena-purple/30 disabled:opacity-50 text-arena-purple text-[10px] font-semibold py-1.5 rounded transition-colors"
                      >
                        {saving ? 'Saving...' : 'Save Changes'}
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* Watchlist Section */}
        {!loading && (
          <div className="p-3 space-y-2">
            <div className="card-base">
              <div className="flex items-center justify-between p-2.5">
                <span className="text-[11px] font-semibold text-arena-purple tracking-wider">WATCHLIST</span>
              </div>
              <div className="px-2.5 pb-3 space-y-2">
                {/* Current symbols */}
                <div className="flex flex-wrap gap-1.5">
                  {(params.watchlist || []).map(sym => (
                    <div key={sym} className="flex items-center gap-1 bg-arena-bg border border-arena-border rounded px-2 py-0.5">
                      <span className="text-[11px] text-white font-mono">{sym}</span>
                      <button
                        onClick={() => removeWatchlistSymbol(sym)}
                        className="text-arena-text-dim hover:text-arena-red transition-colors"
                      >
                        <Trash2 size={10} />
                      </button>
                    </div>
                  ))}
                  {(params.watchlist || []).length === 0 && (
                    <span className="text-[10px] text-arena-text-dim">No symbols in watchlist</span>
                  )}
                </div>
                {/* Add symbol */}
                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    value={watchlistInput}
                    onChange={e => setWatchlistInput(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') addWatchlistSymbol(); }}
                    placeholder="e.g. BTC"
                    className="flex-1 bg-arena-bg border border-arena-border rounded px-2 py-1 text-[11px] text-white focus:outline-none focus:border-arena-purple"
                  />
                  <button
                    onClick={addWatchlistSymbol}
                    className="flex items-center gap-1 bg-arena-purple/20 hover:bg-arena-purple/30 text-arena-purple text-[10px] font-semibold py-1 px-2 rounded transition-colors"
                  >
                    <Plus size={11} /> Add
                  </button>
                </div>
                <button
                  onClick={handleSaveWatchlist}
                  disabled={saving}
                  className="w-full bg-arena-purple/20 hover:bg-arena-purple/30 disabled:opacity-50 text-arena-purple text-[10px] font-semibold py-1.5 rounded transition-colors"
                >
                  {saving ? 'Saving...' : 'Save Watchlist'}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function ParamInput({ label, value, type, onChange }: {
  label: string;
  value: any;
  type: 'number' | 'text';
  onChange: (val: any) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <label className="text-[10px] text-arena-text-dim flex-1">{label}</label>
      <input
        type={type}
        value={value ?? ''}
        onChange={e => onChange(type === 'number' ? parseFloat(e.target.value) || 0 : e.target.value)}
        className="w-24 bg-arena-bg border border-arena-border rounded px-2 py-1 text-[11px] text-white text-right font-mono focus:outline-none focus:border-arena-purple"
      />
    </div>
  );
}
