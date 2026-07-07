import { useState } from 'react'
import type { AgentLeaderboardEntry } from '../hooks/useAgents'
import { Sparkline } from './Sparkline'

interface LeaderboardTableProps {
  agents: AgentLeaderboardEntry[]
  onSelectAgent?: (agent: AgentLeaderboardEntry) => void
}

type SortKey = 'return_pct' | 'max_drawdown' | 'risk_adjusted_score' | 'trade_count' | 'ending_value'

export function LeaderboardTable({ agents, onSelectAgent }: LeaderboardTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>('return_pct')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const sorted = [...agents].sort((a, b) => {
    const aVal = (a[sortKey] || 0) as number
    const bVal = (b[sortKey] || 0) as number
    return sortDir === 'desc' ? bVal - aVal : aVal - bVal
  })

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir(sortDir === 'desc' ? 'asc' : 'desc')
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  if (!agents.length) {
    return <div className="empty-state"><div className="empty-state-icon">🏆</div><div className="empty-state-title">No agents on leaderboard yet</div></div>
  }

  return (
    <div className="leaderboard-table-container">
      <table className="leaderboard-table">
        <thead>
          <tr>
            <th>Rank</th>
            <th>Agent</th>
            <th className="sortable" onClick={() => handleSort('ending_value')}>Portfolio</th>
            <th className="sortable" onClick={() => handleSort('return_pct')}>Return %</th>
            <th className="sortable" onClick={() => handleSort('max_drawdown')}>Max Drawdown</th>
            <th className="sortable" onClick={() => handleSort('risk_adjusted_score')}>Risk Score</th>
            <th className="sortable" onClick={() => handleSort('trade_count')}>Trades</th>
            <th>Equity Curve</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((agent, i) => {
            const returnPct = agent.return_pct || 0
            const historyValues = agent.history?.map((h) => h.value).filter((v) => v > 0) || []
            return (
              <tr
                key={agent.agent_id}
                onClick={() => onSelectAgent?.(agent)}
                style={{ cursor: onSelectAgent ? 'pointer' : 'default' }}
              >
                <td className="rank-cell">#{i + 1}</td>
                <td className="agent-name-cell">{agent.agent_name}</td>
                <td>${(agent.ending_value || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
                <td style={{ color: returnPct >= 0 ? 'var(--success, #22c55e)' : 'var(--danger, #ef4444)', fontWeight: 600 }}>
                  {returnPct >= 0 ? '+' : ''}{returnPct.toFixed(2)}%
                </td>
                <td style={{ color: 'var(--danger, #ef4444)' }}>-{(agent.max_drawdown || 0).toFixed(2)}%</td>
                <td>{(agent.risk_adjusted_score || 0).toFixed(2)}</td>
                <td>{agent.trade_count || 0}</td>
                <td><Sparkline data={historyValues} width={100} height={28} /></td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
