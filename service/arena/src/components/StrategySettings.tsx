import { useEffect, useState } from 'react';
import { Settings, X, RotateCcw, Plus, Trash2, CheckCircle, AlertTriangle } from 'lucide-react';

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
  exposure_controls?: Record<string, any>;
  risk_controls?: Record<string, any>;
}

interface SchemaField {
  type: 'number' | 'text' | 'boolean' | 'select';
  min?: number;
  max?: number;
  default?: any;
  choices?: string[];
  description?: string;
}

interface ConfigSchema {
  schema_name: string;
  display_name: string;
  sections: {
    key: string;
    label: string;
    fields: Record<string, SchemaField>;
  }[];
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
  const [schema, setSchema] = useState<ConfigSchema | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedSection, setExpandedSection] = useState<string | null>('exit_rules');
  const [watchlistInput, setWatchlistInput] = useState('');
  const [backupStatus, setBackupStatus] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      fetch(`/api/agents/manage/${agentId}/strategy-params`, { headers: authHeaders() }).then(r => r.json()),
      fetch(`/api/agents/manage/${agentId}/config-schema`, { headers: authHeaders() }).then(r => {
        if (!r.ok) return null;
        return r.json();
      }).catch(() => null),
      fetch(`/api/agents/manage/${agentId}/config-backup`, { headers: authHeaders() }).then(r => {
        if (!r.ok) return null;
        return r.json();
      }).catch(() => null),
    ])
      .then(([data, schemaData, backupData]) => {
        setParams(data.strategy_params || {});
        if (schemaData) setSchema(schemaData);
        if (backupData) {
          setBackupStatus(backupData.status === 'ok' ? 'ok' : backupData.status);
        }
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

  const sections: { key: string; label: string; fields: { key: string; label: string; type: 'number' | 'text' | 'boolean' | 'select'; choices?: string[]; min?: number; max?: number }[] }[] = schema?.sections?.map(s => ({
    key: s.key,
    label: s.label,
    fields: Object.entries(s.fields).map(([key, field]) => ({
      key,
      label: key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
      type: field.type,
      choices: field.choices,
      min: field.min,
      max: field.max,
    })),
  })) || [
    {
      key: 'exit_rules',
      label: 'Exit Rules',
      fields: [
        { key: 'stop_loss_pct', label: 'Stop Loss %', type: 'number' as const },
        { key: 'take_profit_pct', label: 'Take Profit %', type: 'number' as const },
        { key: 'stagnation_hours', label: 'Stagnation Hours', type: 'number' as const },
        { key: 'stagnation_threshold_pct', label: 'Stagnation Threshold %', type: 'number' as const },
        { key: 'momentum_death_vol_ratio', label: 'Momentum Death Vol Ratio', type: 'number' as const },
        { key: 'ob_exhaustion_rsi', label: 'OB Exhaustion RSI', type: 'number' as const },
        { key: 'trailing_sl_pct', label: 'Trailing SL %', type: 'number' as const },
        { key: 'trailing_activation_pct', label: 'Trailing Activation %', type: 'number' as const },
      ],
    },
    {
      key: 'entry_criteria',
      label: 'Entry Criteria',
      fields: [
        { key: 'min_signals', label: 'Min Signals', type: 'number' as const },
        { key: 'min_signal_families', label: 'Min Signal Families', type: 'number' as const },
        { key: 'min_vol_ratio', label: 'Min Vol Ratio', type: 'number' as const },
        { key: 'direction_mode', label: 'Direction Mode', type: 'select' as const, choices: ['both', 'long', 'short'] },
        { key: 'require_daily_trend_agreement', label: 'Require Daily Trend Agreement', type: 'boolean' as const },
        { key: 'require_btc_regime_ok_for_alts', label: 'Require BTC Regime OK for Alts', type: 'boolean' as const },
        { key: 'require_btc_regime_alignment', label: 'Require BTC Regime Alignment', type: 'boolean' as const },
        { key: 'regime_lookback_days', label: 'Regime Lookback Days', type: 'number' as const },
        { key: 'regime_persistence_bars', label: 'Regime Persistence Bars', type: 'number' as const },
        { key: 'regime_neutral_mode', label: 'Regime Neutral Mode', type: 'select' as const, choices: ['block', 'allow', 'reduce'] },
        { key: 'bearish_macro_min_signals', label: 'Bearish Macro Min Signals', type: 'number' as const },
        { key: 'bearish_macro_threshold', label: 'Bearish Macro Threshold', type: 'number' as const },
        { key: 'min_avg_dollar_volume', label: 'Min Avg Dollar Volume', type: 'number' as const },
      ],
    },
    {
      key: 'position_sizing',
      label: 'Position Sizing',
      fields: [
        { key: 'max_positions', label: 'Max Positions', type: 'number' as const },
        { key: 'normal_sizing_min_pct', label: 'Normal Sizing Min %', type: 'number' as const },
        { key: 'normal_sizing_max_pct', label: 'Normal Sizing Max %', type: 'number' as const },
        { key: 'approaching_sizing_min_pct', label: 'Approaching Sizing Min %', type: 'number' as const },
        { key: 'approaching_sizing_max_pct', label: 'Approaching Sizing Max %', type: 'number' as const },
        { key: 'final_stretch_tp_pct', label: 'Final Stretch TP %', type: 'number' as const },
        { key: 'consecutive_loss_threshold', label: 'Consecutive Loss Threshold', type: 'number' as const },
        { key: 'consecutive_loss_size_cut_pct', label: 'Consecutive Loss Size Cut %', type: 'number' as const },
        { key: 'consecutive_loss_min_signals', label: 'Consecutive Loss Min Signals', type: 'number' as const },
      ],
    },
    {
      key: 'exposure_controls',
      label: 'Exposure Controls',
      fields: [
        { key: 'max_correlated_positions', label: 'Max Correlated Positions', type: 'number' as const },
        { key: 'reserve_btc_slot', label: 'Reserve BTC Slot', type: 'boolean' as const },
      ],
    },
    {
      key: 'switch_logic',
      label: 'Switch Logic',
      fields: [
        { key: 'switch_score_threshold_pct', label: 'Switch Score Threshold %', type: 'number' as const },
        { key: 'reentry_cooldown_hours', label: 'Reentry Cooldown Hours', type: 'number' as const },
      ],
    },
    {
      key: 'scoring_weights',
      label: 'Scoring Weights',
      fields: [
        { key: 'signal_count_weight', label: 'Signal Count Weight', type: 'number' as const },
        { key: 'family_diversity_weight', label: 'Family Diversity Weight', type: 'number' as const },
        { key: 'candle_quality_weight', label: 'Candle Quality Weight', type: 'number' as const },
        { key: 'consolidation_bonus_weight', label: 'Consolidation Bonus Weight', type: 'number' as const },
        { key: 'trend_strength_weight', label: 'Trend Strength Weight', type: 'number' as const },
      ],
    },
    {
      key: 'indicators',
      label: 'Indicators',
      fields: [
        { key: 'rsi_period', label: 'RSI Period', type: 'number' as const },
        { key: 'rsi_bullish', label: 'RSI Bullish Threshold', type: 'number' as const },
        { key: 'rsi_overbought', label: 'RSI Overbought', type: 'number' as const },
        { key: 'rsi_oversold', label: 'RSI Oversold', type: 'number' as const },
        { key: 'macd_fast', label: 'MACD Fast', type: 'number' as const },
        { key: 'macd_slow', label: 'MACD Slow', type: 'number' as const },
        { key: 'macd_signal', label: 'MACD Signal', type: 'number' as const },
        { key: 'stochastic_period', label: 'Stochastic Period', type: 'number' as const },
        { key: 'atr_period', label: 'ATR Period', type: 'number' as const },
        { key: 'vol_ratio_bullish', label: 'Vol Ratio Bullish', type: 'number' as const },
        { key: 'vol_ratio_dead', label: 'Vol Ratio Dead', type: 'number' as const },
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

        {backupStatus && (
          <div className={`m-3 text-[10px] rounded p-2 flex items-center gap-1.5 ${backupStatus === 'ok' ? 'text-green-400 bg-green-400/10' : 'text-amber-400 bg-amber-400/10'}`}>
            {backupStatus === 'ok' ? <CheckCircle size={11} /> : <AlertTriangle size={11} />}
            Config backup: {backupStatus}
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
                          choices={field.choices}
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

function ParamInput({ label, value, type, choices, onChange }: {
  label: string;
  value: any;
  type: 'number' | 'text' | 'boolean' | 'select';
  choices?: string[];
  onChange: (val: any) => void;
}) {
  if (type === 'boolean') {
    return (
      <div className="flex items-center justify-between gap-2">
        <label className="text-[10px] text-arena-text-dim flex-1">{label}</label>
        <button
          onClick={() => onChange(!value)}
          className={`w-12 h-5 rounded-full transition-colors relative ${value ? 'bg-arena-purple' : 'bg-arena-border'}`}
        >
          <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${value ? 'left-6' : 'left-0.5'}`} />
        </button>
      </div>
    );
  }

  if (type === 'select' && choices) {
    return (
      <div className="flex items-center justify-between gap-2">
        <label className="text-[10px] text-arena-text-dim flex-1">{label}</label>
        <select
          value={value ?? ''}
          onChange={e => onChange(e.target.value)}
          className="w-28 bg-arena-bg border border-arena-border rounded px-2 py-1 text-[11px] text-white text-right font-mono focus:outline-none focus:border-arena-purple"
        >
          {choices.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>
    );
  }

  return (
    <div className="flex items-center justify-between gap-2">
      <label className="text-[10px] text-arena-text-dim flex-1">{label}</label>
      <input
        type={type === 'number' ? 'number' : 'text'}
        value={value ?? ''}
        step={type === 'number' ? 'any' : undefined}
        onChange={e => onChange(type === 'number' ? parseFloat(e.target.value) || 0 : e.target.value)}
        className="w-24 bg-arena-bg border border-arena-border rounded px-2 py-1 text-[11px] text-white text-right font-mono focus:outline-none focus:border-arena-purple"
      />
    </div>
  );
}
