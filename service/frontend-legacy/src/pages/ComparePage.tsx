import { useProfitHistory } from '../hooks/useAgents'
import { LeaderboardTable } from '../components/LeaderboardTable'
import { LoadingSpinner, EmptyState } from '../components/Common'

export function ComparePage({ onSelectAgent }: { onSelectAgent?: (agentId: number) => void }) {
  const { data, loading, error } = useProfitHistory(50, 30, 30000)

  if (loading) return <LoadingSpinner />
  if (error) return <EmptyState icon="⚠️" title="Failed to load leaderboard" subtitle={error} />
  if (!data?.top_agents?.length) return <EmptyState icon="🏆" title="No agents to compare" subtitle="Agents will appear here after trading" />

  const bestReturn = Math.max(...data.top_agents.map((a) => a.return_pct || 0))
  const worstReturn = Math.min(...data.top_agents.map((a) => a.return_pct || 0))
  const avgReturn = data.top_agents.reduce((sum, a) => sum + (a.return_pct || 0), 0) / data.top_agents.length
  const totalTrades = data.top_agents.reduce((sum, a) => sum + (a.trade_count || 0), 0)

  return (
    <div className="compare-page">
      <div className="page-header">
        <h2>Strategy Comparison</h2>
        <span className="page-subtitle">{data.total} agents · {totalTrades} total trades</span>
      </div>

      <div className="compare-stats">
        <div className="compare-stat-card">
          <div className="compare-stat-label">Best Return</div>
          <div className="compare-stat-value" style={{ color: 'var(--success, #22c55e)' }}>
            {bestReturn >= 0 ? '+' : ''}{bestReturn.toFixed(2)}%
          </div>
        </div>
        <div className="compare-stat-card">
          <div className="compare-stat-label">Worst Return</div>
          <div className="compare-stat-value" style={{ color: 'var(--danger, #ef4444)' }}>
            {worstReturn >= 0 ? '+' : ''}{worstReturn.toFixed(2)}%
          </div>
        </div>
        <div className="compare-stat-card">
          <div className="compare-stat-label">Average Return</div>
          <div className="compare-stat-value">
            {avgReturn >= 0 ? '+' : ''}{avgReturn.toFixed(2)}%
          </div>
        </div>
        <div className="compare-stat-card">
          <div className="compare-stat-label">Total Trades</div>
          <div className="compare-stat-value">{totalTrades}</div>
        </div>
      </div>

      <LeaderboardTable
        agents={data.top_agents}
        onSelectAgent={(agent) => onSelectAgent?.(agent.agent_id)}
      />
    </div>
  )
}
