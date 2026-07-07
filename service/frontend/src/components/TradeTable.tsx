interface TradeRow {
  id?: number
  agent_name?: string
  agent_id?: number
  type?: string
  symbol?: string
  side?: string
  action?: string
  price?: number
  quantity?: number
  content?: string
  timestamp?: number
  created_at?: string
  market?: string
  pnl?: number
}

interface TradeTableProps {
  trades: TradeRow[]
  showAgent?: boolean
  filterable?: boolean
}

export function TradeTable({ trades, showAgent = true }: TradeTableProps) {
  if (!trades.length) {
    return <div className="empty-state"><div className="empty-state-icon">📋</div><div className="empty-state-title">No trades yet</div></div>
  }

  return (
    <div className="trade-table-container">
      <table className="trade-table">
        <thead>
          <tr>
            {showAgent && <th>Agent</th>}
            <th>Action</th>
            <th>Symbol</th>
            <th>Qty</th>
            <th>Price</th>
            <th>Reasoning</th>
            <th>Time</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((trade, i) => {
            const action = trade.action || trade.side || trade.type || '—'
            const isBuy = action.toLowerCase().includes('buy') || action.toLowerCase() === 'long'
            const isSell = action.toLowerCase().includes('sell')
            return (
              <tr key={i}>
                {showAgent && <td className="trade-agent">{trade.agent_name || '—'}</td>}
                <td>
                  <span className={`trade-action ${isBuy ? 'buy' : isSell ? 'sell' : ''}`}>{action}</span>
                </td>
                <td className="trade-symbol">{trade.symbol || '—'}</td>
                <td>{trade.quantity?.toFixed(4) || '—'}</td>
                <td>${trade.price?.toLocaleString(undefined, { maximumFractionDigits: 2 }) || '—'}</td>
                <td className="trade-reasoning">{trade.content?.slice(0, 80)}{trade.content && trade.content.length > 80 ? '...' : ''}</td>
                <td className="trade-time">{formatTime(trade)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function formatTime(trade: TradeRow): string {
  if (trade.created_at) {
    return new Date(trade.created_at).toLocaleString()
  }
  if (trade.timestamp) {
    return new Date(trade.timestamp * 1000).toLocaleString()
  }
  return '—'
}
