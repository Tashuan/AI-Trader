import { useState, useEffect } from 'react'
import { apiGet } from '../hooks/useApi'

interface TechnicalData {
  available: boolean
  symbol: string
  signal?: string
  rsi?: number
  macd?: number
  macd_signal?: number
  macd_histogram?: number
  sma_20?: number
  sma_50?: number
  bb_upper?: number
  bb_lower?: number
  support_levels?: number[]
  resistance_levels?: number[]
  return_5d?: number
  return_20d?: number
  summary?: string
  reason?: string
}

export function TechnicalPanel({ symbol }: { symbol: string }) {
  const [data, setData] = useState<TechnicalData | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!symbol) return
    setLoading(true)
    apiGet<TechnicalData>(`/market-intel/stocks/${symbol}/latest`)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [symbol])

  if (loading) return <div className="loading"><div className="spinner"></div></div>
  if (!data || !data.available) {
    return (
      <div className="empty-state">
        <div className="empty-state-icon">📊</div>
        <div className="empty-state-title">No technical data for {symbol}</div>
        <div className="empty-state-subtitle">{data?.reason || 'Data may be rate-limited on free tier'}</div>
      </div>
    )
  }

  const rsiColor = (data.rsi || 50) < 30 ? 'var(--success, #22c55e)' : (data.rsi || 50) > 70 ? 'var(--danger, #ef4444)' : 'var(--text-primary)'
  const macdBullish = (data.macd_histogram || 0) > 0

  return (
    <div className="technical-panel">
      <div className="technical-header">
        <h3>{data.symbol}</h3>
        <span className={`signal-badge signal-${(data.signal || '').toLowerCase()}`}>{data.signal || 'N/A'}</span>
      </div>

      <div className="technical-grid">
        <div className="tech-stat">
          <span className="tech-label">RSI (14)</span>
          <span className="tech-value" style={{ color: rsiColor }}>{data.rsi?.toFixed(1)}</span>
          <div className="rsi-bar">
            <div className="rsi-fill" style={{ width: `${data.rsi || 0}%`, background: rsiColor }} />
          </div>
        </div>
        <div className="tech-stat">
          <span className="tech-label">MACD</span>
          <span className="tech-value" style={{ color: macdBullish ? 'var(--success, #22c55e)' : 'var(--danger, #ef4444)' }}>
            {data.macd_histogram?.toFixed(4)}
          </span>
          <span className="tech-sub">{macdBullish ? 'Bullish' : 'Bearish'}</span>
        </div>
        <div className="tech-stat">
          <span className="tech-label">SMA 20</span>
          <span className="tech-value">${data.sma_20?.toFixed(2)}</span>
        </div>
        <div className="tech-stat">
          <span className="tech-label">SMA 50</span>
          <span className="tech-value">{data.sma_50 ? `$${data.sma_50.toFixed(2)}` : 'N/A'}</span>
        </div>
        <div className="tech-stat">
          <span className="tech-label">Bollinger</span>
          <span className="tech-value">${data.bb_lower?.toFixed(2)} - ${data.bb_upper?.toFixed(2)}</span>
        </div>
        <div className="tech-stat">
          <span className="tech-label">5d Return</span>
          <span className="tech-value" style={{ color: (data.return_5d || 0) >= 0 ? 'var(--success, #22c55e)' : 'var(--danger, #ef4444)' }}>
            {data.return_5d?.toFixed(2)}%
          </span>
        </div>
      </div>

      {data.support_levels && data.support_levels.length > 0 && (
        <div className="tech-levels">
          <div className="tech-level-group">
            <span className="tech-level-label">Support</span>
            {data.support_levels.slice(0, 3).map((s, i) => (
              <span key={i} className="tech-level support">${s.toFixed(2)}</span>
            ))}
          </div>
          <div className="tech-level-group">
            <span className="tech-level-label">Resistance</span>
            {data.resistance_levels?.slice(0, 3).map((r, i) => (
              <span key={i} className="tech-level resistance">${r.toFixed(2)}</span>
            ))}
          </div>
        </div>
      )}

      {data.summary && (
        <div className="tech-summary">{data.summary}</div>
      )}
    </div>
  )
}
