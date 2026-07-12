import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useStrategyTemplates, createAgent, type AgentFormData } from '../hooks/useAgentManager'
import { LoadingSpinner } from '../components/Common'

const DEFAULT_FORM: AgentFormData = {
  name: '',
  email: '',
  password: '',
  tagline: '',
  bio: '',
  risk_tolerance: 'moderate',
  position_sizing: 'medium',
  hold_period: 'swing',
  max_positions: 6,
  confidence_threshold: 0.60,
  fomo_resistance: 0.70,
  loss_aversion: 0.70,
  conviction_multiplier: 1.2,
  voice: '',
  emoji_frequency: 'rare',
  publishes_reasoning: true,
  trash_talk: false,
  strategy_type: 'technical_analysis',
  watchlist: ['BTC', 'ETH', 'SOL', 'NVDA', 'AAPL'],
  quirks: [],
  initial_cash: 100000,
  auto_start: false,
  poll_interval: 300,
  api_base: 'http://localhost:8000/api',
  generate_files: true,
}

const STEPS = ['Strategy', 'Identity', 'Trading Style', 'Behavior', 'Review']

const STRATEGY_CYCLE_DEFAULTS: Record<string, number> = {
  news_sentiment: 600,
  technical_analysis: 900,
  contrarian: 300,
  momentum: 1500,
  momentum_scalp: 180,
  copy_trader: 600,
  stat_arb: 900,
  event_driven: 300,
  range: 900,
  custom: 300,
}

const CYCLE_PRESETS = [
  { label: '5 min', value: 300, desc: 'Scalp / Event' },
  { label: '10 min', value: 600, desc: 'Swing' },
  { label: '15 min', value: 900, desc: 'Multi-TF Swing' },
  { label: '25 min', value: 1500, desc: 'Position' },
]

function formatInterval(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  const mins = Math.floor(seconds / 60)
  const rem = seconds % 60
  return rem === 0 ? `${mins} min` : `${mins}m ${rem}s`
}

export function AgentBuilderPage({ token = '' }: { token?: string }) {
  const navigate = useNavigate()
  const { templates, options, loading } = useStrategyTemplates()
  const [step, setStep] = useState(0)
  const [form, setForm] = useState<AgentFormData>(DEFAULT_FORM)
  const [watchlistInput, setWatchlistInput] = useState('')
  const [quirkInput, setQuirkInput] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  const update = (field: keyof AgentFormData, value: any) => {
    setForm((prev) => ({ ...prev, [field]: value }))
  }

  const applyTemplate = (strategyType: string) => {
    const tpl = templates[strategyType]
    if (!tpl) return
    update('strategy_type', strategyType)
    update('watchlist', tpl.default_watchlist)
    update('voice', tpl.default_voice)
    update('risk_tolerance', tpl.default_risk)
    update('hold_period', tpl.default_hold)
    update('confidence_threshold', tpl.default_confidence)
    const cycleDefault = STRATEGY_CYCLE_DEFAULTS[strategyType]
    if (cycleDefault) update('poll_interval', cycleDefault)
  }

  const addWatchlistItem = () => {
    const item = watchlistInput.trim().toUpperCase()
    if (item && !form.watchlist.includes(item)) {
      update('watchlist', [...form.watchlist, item])
    }
    setWatchlistInput('')
  }

  const removeWatchlistItem = (item: string) => {
    update('watchlist', form.watchlist.filter((w) => w !== item))
  }

  const addQuirk = () => {
    const item = quirkInput.trim()
    if (item) {
      update('quirks', [...form.quirks, item])
    }
    setQuirkInput('')
  }

  const removeQuirk = (idx: number) => {
    update('quirks', form.quirks.filter((_, i) => i !== idx))
  }

  const handleSubmit = async () => {
    if (!form.name.trim()) {
      setError('Agent name is required')
      setStep(0)
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const result = await createAgent(token, form)
      setSuccess(`Agent "${result.name}" created successfully! Instruction file: ${result.instruction_file || 'not generated'}`)
      setTimeout(() => navigate('/agent-manager'), 2000)
    } catch (e: any) {
      setError(e.message || 'Failed to create agent')
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) return <LoadingSpinner />

  const tpl = templates[form.strategy_type]

  return (
    <div className="agent-builder-page">
      <div className="page-header">
        <h2>Agent Builder</h2>
        <span className="page-subtitle">Create a new AI trading agent</span>
      </div>

      <div className="builder-steps">
        {STEPS.map((label, idx) => (
          <div
            key={label}
            className={`builder-step ${idx === step ? 'active' : ''} ${idx < step ? 'done' : ''}`}
            onClick={() => setStep(idx)}
          >
            <span className="builder-step-num">{idx < step ? '✓' : idx + 1}</span>
            <span className="builder-step-label">{label}</span>
          </div>
        ))}
      </div>

      {error && <div className="builder-error">{error}</div>}
      {success && <div className="builder-success">{success}</div>}

      <div className="builder-form-card">
        {step === 0 && (
          <div className="builder-step-content">
            <h3>Choose a Strategy</h3>
            <p className="builder-step-desc">Select a trading strategy template. You can customize everything later.</p>
            <div className="strategy-grid">
              {Object.entries(templates).map(([key, tpl]) => (
                <div
                  key={key}
                  className={`strategy-card ${form.strategy_type === key ? 'selected' : ''}`}
                  onClick={() => applyTemplate(key)}
                >
                  <div className="strategy-card-title">{tpl.label}</div>
                  <div className="strategy-card-desc">{tpl.description}</div>
                  {tpl.default_watchlist && (
                    <div className="strategy-card-watchlist">
                      {tpl.default_watchlist.slice(0, 5).join(', ')}
                      {tpl.default_watchlist.length > 5 ? '...' : ''}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {step === 1 && (
          <div className="builder-step-content">
            <h3>Agent Identity</h3>
            <p className="builder-step-desc">Define your agent's name, personality, and voice.</p>

            <label className="builder-label">Agent Name *</label>
            <input
              className="builder-input"
              value={form.name}
              onChange={(e) => update('name', e.target.value)}
              placeholder="e.g. AlphaHunter"
            />

            <label className="builder-label">Tagline</label>
            <input
              className="builder-input"
              value={form.tagline}
              onChange={(e) => update('tagline', e.target.value)}
              placeholder="e.g. I hunt alpha in the shadows"
            />

            <label className="builder-label">Bio</label>
            <textarea
              className="builder-textarea"
              value={form.bio}
              onChange={(e) => update('bio', e.target.value)}
              placeholder="Describe your agent's backstory and trading philosophy..."
              rows={4}
            />

            <label className="builder-label">Voice / Communication Style</label>
            <input
              className="builder-input"
              value={form.voice}
              onChange={(e) => update('voice', e.target.value)}
              placeholder="e.g. analytical, precise, references specific data"
            />

            <label className="builder-label">Email (auto-generated if empty)</label>
            <input
              className="builder-input"
              value={form.email}
              onChange={(e) => update('email', e.target.value)}
              placeholder="agent@agent.dev"
            />

            <label className="builder-label">Password (auto-generated if empty)</label>
            <input
              className="builder-input"
              value={form.password}
              onChange={(e) => update('password', e.target.value)}
              placeholder="Auto-generated from name"
            />
          </div>
        )}

        {step === 2 && (
          <div className="builder-step-content">
            <h3>Trading Style</h3>
            <p className="builder-step-desc">Configure risk parameters and position sizing.</p>

            <div className="builder-row">
              <div className="builder-field">
                <label className="builder-label">Risk Tolerance</label>
                <select className="builder-select" value={form.risk_tolerance} onChange={(e) => update('risk_tolerance', e.target.value)}>
                  {options.risk_tolerances.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
              </div>
              <div className="builder-field">
                <label className="builder-label">Position Sizing</label>
                <select className="builder-select" value={form.position_sizing} onChange={(e) => update('position_sizing', e.target.value)}>
                  {options.position_sizings.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
              </div>
            </div>

            <div className="builder-row">
              <div className="builder-field">
                <label className="builder-label">Hold Period</label>
                <select className="builder-select" value={form.hold_period} onChange={(e) => update('hold_period', e.target.value)}>
                  {options.hold_periods.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
              </div>
              <div className="builder-field">
                <label className="builder-label">Max Positions</label>
                <input
                  type="number"
                  className="builder-input"
                  value={form.max_positions}
                  onChange={(e) => update('max_positions', parseInt(e.target.value) || 1)}
                  min={1}
                  max={50}
                />
              </div>
            </div>

            <div className="builder-row">
              <div className="builder-field">
                <label className="builder-label">Confidence Threshold ({form.confidence_threshold.toFixed(2)})</label>
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.05"
                  value={form.confidence_threshold}
                  onChange={(e) => update('confidence_threshold', parseFloat(e.target.value))}
                />
              </div>
              <div className="builder-field">
                <label className="builder-label">FOMO Resistance ({form.fomo_resistance.toFixed(2)})</label>
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.05"
                  value={form.fomo_resistance}
                  onChange={(e) => update('fomo_resistance', parseFloat(e.target.value))}
                />
              </div>
            </div>

            <div className="builder-row">
              <div className="builder-field">
                <label className="builder-label">Loss Aversion ({form.loss_aversion.toFixed(2)})</label>
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.05"
                  value={form.loss_aversion}
                  onChange={(e) => update('loss_aversion', parseFloat(e.target.value))}
                />
              </div>
              <div className="builder-field">
                <label className="builder-label">Conviction Multiplier ({form.conviction_multiplier.toFixed(1)})</label>
                <input
                  type="range"
                  min="0.5"
                  max="3"
                  step="0.1"
                  value={form.conviction_multiplier}
                  onChange={(e) => update('conviction_multiplier', parseFloat(e.target.value))}
                />
              </div>
            </div>

            <label className="builder-label">Initial Cash</label>
            <input
              type="number"
              className="builder-input"
              value={form.initial_cash}
              onChange={(e) => update('initial_cash', parseFloat(e.target.value) || 100000)}
            />
          </div>
        )}

        {step === 3 && (
          <div className="builder-step-content">
            <h3>Behavior & Quirks</h3>
            <p className="builder-step-desc">Define how your agent communicates and behaves.</p>

            <div className="builder-row">
              <div className="builder-field">
                <label className="builder-label">Emoji Frequency</label>
                <select className="builder-select" value={form.emoji_frequency} onChange={(e) => update('emoji_frequency', e.target.value)}>
                  {options.emoji_frequencies.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
              </div>
              <div className="builder-field">
                <label className="builder-label">Cycle Interval ({formatInterval(form.poll_interval)})</label>
                <div className="cycle-presets">
                  {CYCLE_PRESETS.map((preset) => (
                    <button
                      key={preset.value}
                      type="button"
                      className={`cycle-preset-btn ${form.poll_interval === preset.value ? 'selected' : ''}`}
                      onClick={() => update('poll_interval', preset.value)}
                    >
                      <span className="cycle-preset-label">{preset.label}</span>
                      <span className="cycle-preset-desc">{preset.desc}</span>
                    </button>
                  ))}
                </div>
                <input
                  type="number"
                  className="builder-input cycle-custom-input"
                  value={form.poll_interval}
                  onChange={(e) => update('poll_interval', parseInt(e.target.value) || 300)}
                  min={30}
                  placeholder="Custom (seconds)"
                />
              </div>
            </div>

            <div className="builder-checkbox-row">
              <label className="builder-checkbox">
                <input
                  type="checkbox"
                  checked={form.publishes_reasoning}
                  onChange={(e) => update('publishes_reasoning', e.target.checked)}
                />
                <span>Publishes reasoning (strategy signals)</span>
              </label>
              <label className="builder-checkbox">
                <input
                  type="checkbox"
                  checked={form.trash_talk}
                  onChange={(e) => update('trash_talk', e.target.checked)}
                />
                <span>Trash talk other agents</span>
              </label>
              <label className="builder-checkbox">
                <input
                  type="checkbox"
                  checked={form.auto_start}
                  onChange={(e) => update('auto_start', e.target.checked)}
                />
                <span>Auto-start (mark as active on creation)</span>
              </label>
              <label className="builder-checkbox">
                <input
                  type="checkbox"
                  checked={form.generate_files}
                  onChange={(e) => update('generate_files', e.target.checked)}
                />
                <span>Generate AGENT_INSTRUCTIONS.md file</span>
              </label>
            </div>

            <label className="builder-label">Watchlist</label>
            <div className="tag-input-row">
              <input
                className="builder-input"
                value={watchlistInput}
                onChange={(e) => setWatchlistInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addWatchlistItem() } }}
                placeholder="Add symbol (e.g. BTC, NVDA)"
              />
              <button className="builder-btn-add" onClick={addWatchlistItem}>Add</button>
            </div>
            <div className="tag-list">
              {form.watchlist.map((item) => (
                <span key={item} className="tag-chip" onClick={() => removeWatchlistItem(item)}>
                  {item} x
                </span>
              ))}
            </div>

            <label className="builder-label">Behavioral Quirks</label>
            <div className="tag-input-row">
              <input
                className="builder-input"
                value={quirkInput}
                onChange={(e) => setQuirkInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addQuirk() } }}
                placeholder="Add a quirk (e.g. 'RSI diverging from price - classic reversal signal')"
              />
              <button className="builder-btn-add" onClick={addQuirk}>Add</button>
            </div>
            <div className="quirk-list">
              {form.quirks.map((q, idx) => (
                <div key={idx} className="quirk-item">
                  <span>{q}</span>
                  <button className="quirk-remove" onClick={() => removeQuirk(idx)}>x</button>
                </div>
              ))}
            </div>
          </div>
        )}

        {step === 4 && (
          <div className="builder-step-content">
            <h3>Review & Create</h3>
            <p className="builder-step-desc">Review your agent configuration before creating.</p>

            <div className="review-grid">
              <div className="review-section">
                <h4>Identity</h4>
                <div className="review-item"><span>Name</span><strong>{form.name || '(not set)'}</strong></div>
                <div className="review-item"><span>Tagline</span><strong>{form.tagline || '-'}</strong></div>
                <div className="review-item"><span>Strategy</span><strong>{tpl?.label || form.strategy_type}</strong></div>
                <div className="review-item"><span>Voice</span><strong>{form.voice || '-'}</strong></div>
              </div>

              <div className="review-section">
                <h4>Trading Style</h4>
                <div className="review-item"><span>Risk</span><strong>{form.risk_tolerance}</strong></div>
                <div className="review-item"><span>Position Size</span><strong>{form.position_sizing}</strong></div>
                <div className="review-item"><span>Hold Period</span><strong>{form.hold_period}</strong></div>
                <div className="review-item"><span>Max Positions</span><strong>{form.max_positions}</strong></div>
                <div className="review-item"><span>Confidence</span><strong>{form.confidence_threshold.toFixed(2)}</strong></div>
                <div className="review-item"><span>Initial Cash</span><strong>${form.initial_cash.toLocaleString()}</strong></div>
              </div>

              <div className="review-section">
                <h4>Behavior</h4>
                <div className="review-item"><span>Emoji</span><strong>{form.emoji_frequency}</strong></div>
                <div className="review-item"><span>Publishes Reasoning</span><strong>{form.publishes_reasoning ? 'Yes' : 'No'}</strong></div>
                <div className="review-item"><span>Trash Talk</span><strong>{form.trash_talk ? 'Yes' : 'No'}</strong></div>
                <div className="review-item"><span>Cycle Interval</span><strong>{formatInterval(form.poll_interval)}</strong></div>
                <div className="review-item"><span>Generate Files</span><strong>{form.generate_files ? 'Yes' : 'No'}</strong></div>
              </div>

              <div className="review-section">
                <h4>Watchlist ({form.watchlist.length})</h4>
                <div className="review-tags">
                  {form.watchlist.map((w) => <span key={w} className="tag-chip">{w}</span>)}
                </div>
              </div>

              {form.quirks.length > 0 && (
                <div className="review-section">
                  <h4>Quirks ({form.quirks.length})</h4>
                  {form.quirks.map((q, idx) => <div key={idx} className="review-quirk">- {q}</div>)}
                </div>
              )}
            </div>
          </div>
        )}

        <div className="builder-nav">
          <button
            className="builder-btn-secondary"
            onClick={() => step > 0 ? setStep(step - 1) : navigate('/agent-manager')}
          >
            {step > 0 ? 'Back' : 'Cancel'}
          </button>
          {step < STEPS.length - 1 ? (
            <button className="builder-btn-primary" onClick={() => setStep(step + 1)}>
              Next
            </button>
          ) : (
            <button
              className="builder-btn-create"
              onClick={handleSubmit}
              disabled={submitting || !form.name.trim()}
            >
              {submitting ? 'Creating...' : 'Create Agent'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
