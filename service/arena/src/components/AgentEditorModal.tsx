import { useState, useEffect, useCallback } from 'react';
import { X, Save, Plus, Trash2, Loader2 } from 'lucide-react';

interface AgentEditorModalProps {
  agentId: number;
  agentName: string;
  onClose: () => void;
  onSaved?: () => void;
}

interface AgentEditData {
  tagline: string;
  bio: string;
  strategy_type: string;
  risk_tolerance: string;
  position_sizing: string;
  hold_period: string;
  max_positions: number;
  confidence_threshold: number;
  fomo_resistance: number;
  loss_aversion: number;
  conviction_multiplier: number;
  voice: string;
  emoji_frequency: string;
  publishes_reasoning: boolean;
  trash_talk: boolean;
  watchlist: string[];
  quirks: string[];
  status: string;
  auto_start: boolean;
  poll_interval: number;
  api_base: string;
}

const STRATEGY_OPTIONS = [
  { value: 'news_sentiment', label: 'News Sentiment' },
  { value: 'technical_analysis', label: 'Technical Analysis' },
  { value: 'contrarian', label: 'Contrarian / Fade' },
  { value: 'momentum', label: 'Momentum / Trend' },
  { value: 'momentum_scalp', label: 'Momentum Scalp (Blitz)' },
  { value: 'copy_trader', label: 'Copy Trader' },
  { value: 'stat_arb', label: 'Stat Arb / Pairs' },
  { value: 'event_driven', label: 'Event-Driven' },
  { value: 'range', label: 'Range / Grid' },
  { value: 'custom', label: 'Custom' },
];

const RISK_OPTIONS = ['conservative', 'moderate', 'aggressive', 'degen'];
const SIZING_OPTIONS = ['small', 'medium', 'large', 'yolo'];
const HOLD_OPTIONS = ['scalp', 'swing', 'position', 'long-term'];
const EMOJI_OPTIONS = ['none', 'rare', 'occasional', 'frequent', 'excessive'];
const STATUS_OPTIONS = ['active', 'inactive'];

function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const token = localStorage.getItem('auth_token');
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return headers;
}

export function AgentEditorModal({ agentId, agentName, onClose, onSaved }: AgentEditorModalProps) {
  const [data, setData] = useState<AgentEditData | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [watchlistInput, setWatchlistInput] = useState('');
  const [quirkInput, setQuirkInput] = useState('');

  const fetchDetail = useCallback(async () => {
    try {
      const res = await fetch(`/api/agents/manage/${agentId}`, { headers: authHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = await res.json();
      setData({
        tagline: d.tagline || '',
        bio: d.bio || '',
        strategy_type: d.strategy_type || 'custom',
        risk_tolerance: d.risk_tolerance || 'moderate',
        position_sizing: d.position_sizing || 'medium',
        hold_period: d.hold_period || 'swing',
        max_positions: d.max_positions || 6,
        confidence_threshold: d.confidence_threshold ?? 0.6,
        fomo_resistance: d.fomo_resistance ?? 0.7,
        loss_aversion: d.loss_aversion ?? 0.7,
        conviction_multiplier: d.conviction_multiplier ?? 1.2,
        voice: d.voice || '',
        emoji_frequency: d.emoji_frequency || 'rare',
        publishes_reasoning: d.publishes_reasoning ?? true,
        trash_talk: d.trash_talk ?? false,
        watchlist: d.watchlist || [],
        quirks: d.quirks || [],
        status: d.status || 'inactive',
        auto_start: d.auto_start ?? false,
        poll_interval: d.poll_interval || 300,
        api_base: d.api_base || 'http://localhost:8000/api',
      });
    } catch (e: any) {
      setError(e.message || 'Failed to load agent config');
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    fetchDetail();
  }, [fetchDetail]);

  const update = <K extends keyof AgentEditData>(field: K, value: AgentEditData[K]) => {
    setData(prev => prev ? { ...prev, [field]: value } : prev);
  };

  const addWatchlistItem = () => {
    const item = watchlistInput.trim().toUpperCase();
    if (item && data && !data.watchlist.includes(item)) {
      update('watchlist', [...data.watchlist, item]);
    }
    setWatchlistInput('');
  };

  const removeWatchlistItem = (item: string) => {
    if (data) update('watchlist', data.watchlist.filter(w => w !== item));
  };

  const addQuirk = () => {
    const item = quirkInput.trim();
    if (item && data) {
      update('quirks', [...data.quirks, item]);
    }
    setQuirkInput('');
  };

  const removeQuirk = (idx: number) => {
    if (data) update('quirks', data.quirks.filter((_, i) => i !== idx));
  };

  const handleSave = async () => {
    if (!data) return;
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      const res = await fetch(`/api/agents/manage/${agentId}`, {
        method: 'PUT',
        headers: authHeaders(),
        body: JSON.stringify(data),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(err.detail || 'Failed to save');
      }
      setSuccess('Saved successfully');
      setTimeout(() => {
        onSaved?.();
        onClose();
      }, 800);
    } catch (e: any) {
      setError(e.message || 'Failed to save');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/70" onClick={onClose} />
      <div className="relative w-full max-w-lg bg-arena-card border-l border-arena-border overflow-y-auto drawer-slide-in">
        {/* Header */}
        <div className="sticky top-0 z-10 bg-arena-card border-b border-arena-border p-4 flex items-center justify-between">
          <div>
            <div className="text-sm font-bold text-white">Edit Agent</div>
            <div className="text-[10px] text-arena-text-dim">{agentName}</div>
          </div>
          <button onClick={onClose} className="text-arena-text-dim hover:text-white transition-colors">
            <X size={18} />
          </button>
        </div>

        {loading && (
          <div className="p-8 flex items-center justify-center gap-2 text-arena-text-dim text-sm">
            <Loader2 size={14} className="animate-spin" /> Loading config...
          </div>
        )}

        {error && (
          <div className="m-4 p-3 rounded-lg bg-arena-red/10 border border-arena-red/30 text-arena-red text-[11px]">
            {error}
          </div>
        )}

        {success && (
          <div className="m-4 p-3 rounded-lg bg-arena-green/10 border border-arena-green/30 text-arena-green text-[11px]">
            {success}
          </div>
        )}

        {data && !loading && (
          <div className="p-4 space-y-5">
            {/* Identity */}
            <FormSection title="Identity">
              <FormField label="Tagline">
                <input className="form-input" value={data.tagline} onChange={e => update('tagline', e.target.value)} placeholder="Short one-liner" />
              </FormField>
              <FormField label="Bio">
                <textarea className="form-input resize-none" rows={2} value={data.bio} onChange={e => update('bio', e.target.value)} placeholder="Agent personality description" />
              </FormField>
              <FormField label="Strategy Type">
                <select className="form-input" value={data.strategy_type} onChange={e => update('strategy_type', e.target.value)}>
                  {STRATEGY_OPTIONS.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
                </select>
              </FormField>
              <FormField label="Voice">
                <input className="form-input" value={data.voice} onChange={e => update('voice', e.target.value)} placeholder="e.g. analytical and news-driven" />
              </FormField>
              <FormField label="Emoji Frequency">
                <select className="form-input" value={data.emoji_frequency} onChange={e => update('emoji_frequency', e.target.value)}>
                  {EMOJI_OPTIONS.map(opt => <option key={opt} value={opt}>{opt}</option>)}
                </select>
              </FormField>
              <div className="flex items-center gap-4">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={data.publishes_reasoning} onChange={e => update('publishes_reasoning', e.target.checked)} className="form-checkbox" />
                  <span className="text-[11px] text-arena-text-secondary">Publishes reasoning</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={data.trash_talk} onChange={e => update('trash_talk', e.target.checked)} className="form-checkbox" />
                  <span className="text-[11px] text-arena-text-secondary">Trash talk</span>
                </label>
              </div>
            </FormSection>

            {/* Trading Style */}
            <FormSection title="Trading Style">
              <div className="grid grid-cols-2 gap-3">
                <FormField label="Risk Tolerance">
                  <select className="form-input" value={data.risk_tolerance} onChange={e => update('risk_tolerance', e.target.value)}>
                    {RISK_OPTIONS.map(opt => <option key={opt} value={opt}>{opt}</option>)}
                  </select>
                </FormField>
                <FormField label="Position Sizing">
                  <select className="form-input" value={data.position_sizing} onChange={e => update('position_sizing', e.target.value)}>
                    {SIZING_OPTIONS.map(opt => <option key={opt} value={opt}>{opt}</option>)}
                  </select>
                </FormField>
                <FormField label="Hold Period">
                  <select className="form-input" value={data.hold_period} onChange={e => update('hold_period', e.target.value)}>
                    {HOLD_OPTIONS.map(opt => <option key={opt} value={opt}>{opt}</option>)}
                  </select>
                </FormField>
                <FormField label="Max Positions">
                  <input type="number" className="form-input" value={data.max_positions} min={1} max={50} onChange={e => update('max_positions', parseInt(e.target.value) || 1)} />
                </FormField>
              </div>
            </FormSection>

            {/* Behavioral Parameters */}
            <FormSection title="Behavioral Parameters">
              <SliderField label="Confidence Threshold" value={data.confidence_threshold} min={0} max={1} step={0.05} onChange={v => update('confidence_threshold', v)} />
              <SliderField label="FOMO Resistance" value={data.fomo_resistance} min={0} max={1} step={0.05} onChange={v => update('fomo_resistance', v)} />
              <SliderField label="Loss Aversion" value={data.loss_aversion} min={0} max={1} step={0.05} onChange={v => update('loss_aversion', v)} />
              <SliderField label="Conviction Multiplier" value={data.conviction_multiplier} min={0.5} max={3} step={0.1} onChange={v => update('conviction_multiplier', v)} />
            </FormSection>

            {/* Watchlist */}
            <FormSection title="Watchlist">
              <div className="flex gap-2">
                <input
                  className="form-input flex-1"
                  value={watchlistInput}
                  onChange={e => setWatchlistInput(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addWatchlistItem(); } }}
                  placeholder="Add symbol (e.g. BTC)"
                />
                <button onClick={addWatchlistItem} className="form-btn-secondary">
                  <Plus size={12} />
                </button>
              </div>
              {data.watchlist.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {data.watchlist.map(sym => (
                    <span key={sym} className="flex items-center gap-1 text-[10px] font-mono px-2 py-0.5 bg-arena-bg rounded text-arena-text-secondary">
                      {sym}
                      <button onClick={() => removeWatchlistItem(sym)} className="text-arena-text-dim hover:text-arena-red transition-colors">
                        <X size={10} />
                      </button>
                    </span>
                  ))}
                </div>
              )}
            </FormSection>

            {/* Quirks */}
            <FormSection title="Quirks">
              <div className="flex gap-2">
                <input
                  className="form-input flex-1"
                  value={quirkInput}
                  onChange={e => setQuirkInput(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addQuirk(); } }}
                  placeholder="Add quirk"
                />
                <button onClick={addQuirk} className="form-btn-secondary">
                  <Plus size={12} />
                </button>
              </div>
              {data.quirks.length > 0 && (
                <div className="space-y-1 mt-2">
                  {data.quirks.map((q, idx) => (
                    <div key={idx} className="flex items-center justify-between text-[11px] text-arena-text-secondary px-2 py-1 bg-arena-bg rounded">
                      <span className="italic">{q}</span>
                      <button onClick={() => removeQuirk(idx)} className="text-arena-text-dim hover:text-arena-red transition-colors">
                        <Trash2 size={11} />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </FormSection>

            {/* Runtime Config */}
            <FormSection title="Runtime">
              <div className="grid grid-cols-2 gap-3">
                <FormField label="Status">
                  <select className="form-input" value={data.status} onChange={e => update('status', e.target.value)}>
                    {STATUS_OPTIONS.map(opt => <option key={opt} value={opt}>{opt}</option>)}
                  </select>
                </FormField>
                <FormField label="Poll Interval (sec)">
                  <input type="number" className="form-input" value={data.poll_interval} min={30} max={3600} onChange={e => update('poll_interval', parseInt(e.target.value) || 300)} />
                </FormField>
              </div>
              <FormField label="API Base">
                <input className="form-input" value={data.api_base} onChange={e => update('api_base', e.target.value)} />
              </FormField>
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={data.auto_start} onChange={e => update('auto_start', e.target.checked)} className="form-checkbox" />
                <span className="text-[11px] text-arena-text-secondary">Auto-start</span>
              </label>
            </FormSection>

            {/* Save Button */}
            <div className="sticky bottom-0 bg-arena-card border-t border-arena-border p-3 -mx-4 -mb-4 flex justify-end gap-2">
              <button onClick={onClose} className="form-btn-secondary px-4 py-2 text-[11px]">
                Cancel
              </button>
              <button
                onClick={handleSave}
                disabled={saving}
                className="flex items-center gap-1.5 px-4 py-2 bg-arena-purple/20 text-arena-purple rounded-lg hover:bg-arena-purple/30 transition-colors text-[11px] font-semibold disabled:opacity-50"
              >
                {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
                {saving ? 'Saving...' : 'Save Changes'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function FormSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card-base p-3 space-y-3">
      <div className="text-[10px] font-semibold text-arena-purple tracking-wider">{title.toUpperCase()}</div>
      {children}
    </div>
  );
}

function FormField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-[10px] text-arena-text-dim mb-1">{label}</label>
      {children}
    </div>
  );
}

function SliderField({ label, value, min, max, step, onChange }: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <label className="text-[10px] text-arena-text-dim">{label}</label>
        <span className="text-[10px] font-mono text-arena-text-secondary">{value.toFixed(2)}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={e => onChange(parseFloat(e.target.value))}
        className="form-slider w-full"
      />
    </div>
  );
}
