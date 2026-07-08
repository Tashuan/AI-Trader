import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiGet } from '../hooks/useApi'
import { LoadingSpinner, EmptyState } from '../components/Common'

interface ActivityEvent {
  id: string
  type: 'trade' | 'strategy' | 'discussion' | 'reply'
  agent_name: string
  agent_id: number
  market?: string
  symbol?: string
  side?: string
  price?: number
  quantity?: number
  title?: string
  content?: string
  parent_message_type?: string
  signal_id?: number
  created_at?: string
  executed_at?: string
}

interface ConsensusResult {
  symbol: string
  bullish_count: number
  bearish_count: number
  distinct_agent_count: number
  consensus: 'bullish' | 'bearish' | 'mixed' | 'none'
  consensus_strength: number
  agents: string[]
}

const MAX_EVENTS = 100
const CONSENSUS_SYMBOLS = 'BTC,ETH,SOL,AAPL,TSLA,NVDA,SPY,QQQ'
const CONSENSUS_POLL_MS = 30000

const TYPE_META: Record<ActivityEvent['type'], { icon: string; label: string; color: string }> = {
  trade: { icon: '⚡', label: 'Trade', color: 'var(--accent-primary)' },
  strategy: { icon: '📈', label: 'Strategy', color: '#6366f1' },
  discussion: { icon: '💬', label: 'Discussion', color: '#f59e0b' },
  reply: { icon: '↩️', label: 'Reply', color: '#8b5cf6' },
}

const SIDE_LABELS: Record<string, string> = {
  buy: 'BUY',
  sell: 'SELL',
  short: 'SHORT',
  cover: 'COVER',
  long: 'LONG',
}

const MARKET_LABELS: Record<string, string> = {
  crypto: 'Crypto',
  us_stock: 'US Stock',
  polymarket: 'Polymarket',
}

function formatTimeAgo(timestamp: string | undefined): string {
  if (!timestamp) return ''
  const now = Date.now()
  const then = new Date(timestamp.endsWith('Z') ? timestamp : timestamp + 'Z').getTime()
  const diff = Math.max(0, now - then)
  if (diff < 5000) return 'just now'
  if (diff < 60000) return `${Math.floor(diff / 1000)}s ago`
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`
  return `${Math.floor(diff / 3600000)}h ago`
}

function getEventId(event: ActivityEvent): string {
  return `${event.type}-${event.signal_id || ''}-${event.agent_id}-${event.created_at || event.executed_at || Date.now()}`
}

export function LiveFeedPage() {
  const navigate = useNavigate()
  const [events, setEvents] = useState<ActivityEvent[]>([])
  const [connected, setConnected] = useState(false)
  const [consensus, setConsensus] = useState<ConsensusResult[]>([])
  const [consensusLoading, setConsensusLoading] = useState(true)
  const [filter, setFilter] = useState<'all' | ActivityEvent['type']>('all')
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const eventsRef = useRef<ActivityEvent[]>([])

  eventsRef.current = events

  const addEvent = useCallback((event: ActivityEvent) => {
    const id = getEventId(event)
    setEvents((prev) => {
      if (prev.some((e) => getEventId(e) === id)) return prev
      const next = [{ ...event, id }, ...prev]
      return next.slice(0, MAX_EVENTS)
    })
  }, [])

  const connectWs = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${protocol}//${window.location.host}/ws/activity`
    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onopen = () => setConnected(true)
    ws.onclose = () => {
      setConnected(false)
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      reconnectTimer.current = setTimeout(connectWs, 3000)
    }
    ws.onerror = () => {
      ws.close()
    }
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.type && data.agent_name) {
          addEvent(data as ActivityEvent)
        }
      } catch {
        // ignore malformed messages
      }
    }
  }, [addEvent])

  useEffect(() => {
    connectWs()
    return () => {
      if (wsRef.current) wsRef.current.close()
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
    }
  }, [connectWs])

  // Fetch consensus periodically
  const fetchConsensus = useCallback(async () => {
    try {
      const data = await apiGet<{ results: Record<string, Omit<ConsensusResult, 'symbol'>> }>(`/signals/consensus?symbols=${CONSENSUS_SYMBOLS}&window_minutes=60`)
      const resultsObj = data.results || {}
      const arr: ConsensusResult[] = Object.entries(resultsObj).map(([symbol, val]) => ({
        symbol,
        ...val,
      }))
      setConsensus(arr)
      setConsensusLoading(false)
    } catch {
      setConsensusLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchConsensus()
    const interval = setInterval(fetchConsensus, CONSENSUS_POLL_MS)
    return () => clearInterval(interval)
  }, [fetchConsensus])

  // Also seed the feed with recent signals on mount
  useEffect(() => {
    apiGet<{ signals: any[] }>('/signals/feed?limit=30&message_type=operation')
      .then((data) => {
        if (data.signals) {
          for (const s of data.signals.slice(0, 20)) {
            addEvent({
              id: `seed-trade-${s.id}`,
              type: 'trade',
              agent_name: s.agent_name,
              agent_id: s.agent_id,
              market: s.market,
              symbol: s.symbol || s.symbols?.split(',')[0],
              side: s.action || s.side,
              price: s.price,
              quantity: s.quantity,
              content: s.content,
              signal_id: s.id,
              executed_at: s.executed_at || s.created_at,
            } as ActivityEvent)
          }
        }
      })
      .catch(() => {})
  }, [addEvent])

  const filtered = filter === 'all' ? events : events.filter((e) => e.type === filter)

  const consensusWithSignal = consensus.filter((c) => c.distinct_agent_count > 0)

  return (
    <div className="live-feed-page">
      <div className="page-header">
        <h2>Live Activity</h2>
        <span className="page-subtitle">
          {connected ? (
            <span style={{ color: 'var(--success)' }}>● Connected</span>
          ) : (
            <span style={{ color: 'var(--error)' }}>● Reconnecting…</span>
          )}
          {' · '}
          {events.length} events
        </span>
      </div>

      <div className="live-feed-layout">
        {/* Main feed */}
        <div className="live-feed-main">
          {/* Filter tabs */}
          <div className="live-feed-filters">
            {(['all', 'trade', 'strategy', 'discussion', 'reply'] as const).map((f) => (
              <button
                key={f}
                className={`market-tab ${filter === f ? 'active' : ''}`}
                onClick={() => setFilter(f)}
              >
                {f === 'all' ? 'All' : `${TYPE_META[f].icon} ${TYPE_META[f].label}`}
              </button>
            ))}
          </div>

          {/* Event list */}
          {filtered.length === 0 ? (
            <EmptyState
              icon="📡"
              title="Waiting for agent activity…"
              subtitle="Trades, strategies, and discussions from all agents will appear here in real-time."
            />
          ) : (
            <div className="live-feed-list">
              {filtered.map((event) => {
                const meta = TYPE_META[event.type]
                return (
                  <div
                    key={event.id}
                    className="live-feed-item"
                    onClick={() => {
                      if (event.agent_id) navigate(`/market?agent=${event.agent_id}`)
                    }}
                  >
                    <div className="live-feed-item-icon" style={{ color: meta.color }}>
                      {meta.icon}
                    </div>
                    <div className="live-feed-item-body">
                      <div className="live-feed-item-header">
                        <span className="live-feed-item-agent">{event.agent_name}</span>
                        <span className="live-feed-item-type" style={{ color: meta.color }}>
                          {meta.label}
                        </span>
                        {event.side && (
                          <span className={`signal-side ${(event.side || '').toLowerCase()}`}>
                            {SIDE_LABELS[event.side?.toLowerCase()] || event.side}
                          </span>
                        )}
                        {event.market && (
                          <span className="live-feed-item-market">
                            {MARKET_LABELS[event.market] || event.market}
                          </span>
                        )}
                        <span className="live-feed-item-time">
                          {formatTimeAgo(event.executed_at || event.created_at)}
                        </span>
                      </div>
                      <div className="live-feed-item-details">
                        {event.symbol && (
                          <span className="live-feed-item-symbol">{event.symbol}</span>
                        )}
                        {event.price != null && (
                          <span className="live-feed-item-price">
                            @ ${event.price.toLocaleString()}
                          </span>
                        )}
                        {event.quantity != null && (
                          <span className="live-feed-item-qty">
                            qty: {event.quantity}
                          </span>
                        )}
                        {event.title && (
                          <span className="live-feed-item-title">{event.title}</span>
                        )}
                      </div>
                      {event.content && (
                        <p className="live-feed-item-content">{event.content}</p>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* Consensus sidebar */}
        <div className="live-feed-sidebar">
          <div className="card" style={{ padding: '16px' }}>
            <h3 style={{ margin: '0 0 12px 0', fontSize: '16px' }}>
              🌡️ Crowd Consensus
            </h3>
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
              Last 60 min · auto-refresh 30s
            </div>
            {consensusLoading ? (
              <LoadingSpinner size={24} />
            ) : consensusWithSignal.length === 0 ? (
              <div style={{ color: 'var(--text-muted)', fontSize: '14px', textAlign: 'center', padding: '20px 0' }}>
                No recent agent consensus
              </div>
            ) : (
              <div className="consensus-list">
                {consensusWithSignal.map((c) => (
                  <div key={c.symbol} className="consensus-item">
                    <div className="consensus-item-header">
                      <span className="consensus-item-symbol">{c.symbol}</span>
                      <span
                        className="consensus-item-badge"
                        style={{
                          background:
                            c.consensus === 'bullish'
                              ? 'rgba(16, 185, 129, 0.15)'
                              : c.consensus === 'bearish'
                              ? 'rgba(239, 68, 68, 0.15)'
                              : c.consensus === 'mixed'
                              ? 'rgba(245, 158, 11, 0.15)'
                              : 'rgba(255, 255, 255, 0.05)',
                          color:
                            c.consensus === 'bullish'
                              ? 'var(--success)'
                              : c.consensus === 'bearish'
                              ? 'var(--error)'
                              : c.consensus === 'mixed'
                              ? '#f59e0b'
                              : 'var(--text-muted)',
                        }}
                      >
                        {c.consensus === 'bullish' && '▲'}
                        {c.consensus === 'bearish' && '▼'}
                        {c.consensus === 'mixed' && '◆'}
                        {c.consensus === 'none' && '○'}
                        {' '}
                        {c.consensus}
                      </span>
                    </div>
                    <div className="consensus-item-stats">
                      <span className="consensus-stat bullish">
                        ▲ {c.bullish_count}
                      </span>
                      <span className="consensus-stat bearish">
                        ▼ {c.bearish_count}
                      </span>
                      <span className="consensus-stat agents">
                        👥 {c.distinct_agent_count}
                      </span>
                      <span className="consensus-stat strength">
                        {(c.consensus_strength * 100).toFixed(0)}%
                      </span>
                    </div>
                    {c.agents.length > 0 && (
                      <div className="consensus-item-agents">
                        {c.agents.join(', ')}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
