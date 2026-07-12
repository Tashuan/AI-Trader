import { useState, useEffect } from 'react'
import { apiGet } from '../hooks/useApi'
import { TradeTable } from '../components/TradeTable'
import { LoadingSpinner, EmptyState } from '../components/Common'

interface SignalEntry {
  id: number
  agent_name: string
  agent_id: number
  message_type: string
  market: string
  title: string
  content: string
  symbols: string
  tags: string
  created_at: string
  action?: string
  symbol?: string
  price?: number
  quantity?: number
}

export function TradeLogPage() {
  const [trades, setTrades] = useState<SignalEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filterAgent, setFilterAgent] = useState<string>('all')
  const [filterAction, setFilterAction] = useState<string>('all')

  useEffect(() => {
    apiGet<{ signals: SignalEntry[] }>('/signals/feed?limit=100&message_type=operation')
      .then((data) => setTrades(data.signals || []))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const agents = [...new Set(trades.map((t) => t.agent_name))]

  const filtered = trades.filter((t) => {
    if (filterAgent !== 'all' && t.agent_name !== filterAgent) return false
    if (filterAction !== 'all') {
      const action = (t.action || '').toLowerCase()
      if (filterAction === 'buy' && !action.includes('buy')) return false
      if (filterAction === 'sell' && !action.includes('sell')) return false
    }
    return true
  })

  const tradeRows = filtered.map((t) => ({
    agent_name: t.agent_name,
    action: t.action || t.title?.split(' ')[0] || 'trade',
    symbol: t.symbol || t.symbols?.split(',')[0],
    price: t.price,
    quantity: t.quantity,
    content: t.content,
    created_at: t.created_at,
    market: t.market,
  }))

  if (loading) return <LoadingSpinner />
  if (error) return <EmptyState icon="⚠️" title="Failed to load trades" subtitle={error} />

  return (
    <div className="trade-log-page">
      <div className="page-header">
        <h2>Trade Log</h2>
        <span className="page-subtitle">{filtered.length} trades</span>
      </div>

      <div className="trade-log-filters">
        <select value={filterAgent} onChange={(e) => setFilterAgent(e.target.value)} className="filter-select">
          <option value="all">All Agents</option>
          {agents.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
        <select value={filterAction} onChange={(e) => setFilterAction(e.target.value)} className="filter-select">
          <option value="all">All Actions</option>
          <option value="buy">Buys Only</option>
          <option value="sell">Sells Only</option>
        </select>
        <button
          className="export-btn"
          onClick={() => exportCsv(tradeRows)}
          disabled={!tradeRows.length}
        >
          Export CSV
        </button>
      </div>

      <TradeTable trades={tradeRows} />
    </div>
  )
}

function exportCsv(rows: any[]) {
  const headers = ['Agent', 'Action', 'Symbol', 'Quantity', 'Price', 'Reasoning', 'Time']
  const lines = [headers.join(',')]
  for (const r of rows) {
    const vals = [r.agent_name, r.action, r.symbol, r.quantity, r.price, `"${r.content?.replace(/"/g, '""')}"`, r.created_at]
    lines.push(vals.join(','))
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `trades-${Date.now()}.csv`
  a.click()
  URL.revokeObjectURL(url)
}
