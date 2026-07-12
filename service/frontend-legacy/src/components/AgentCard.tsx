import type { AgentLeaderboardEntry } from '../hooks/useAgents'
import { PnLDisplay } from './Common'
import { Sparkline } from './Sparkline'

interface AgentCardProps {
  agent: AgentLeaderboardEntry
  onClick?: () => void
}

const STRATEGY_ICONS: Record<string, string> = {
  news_sentiment: '📰',
  technical_analysis: '📊',
  contrarian: '🔄',
  momentum: '🚀',
  copy_trader: '📋',
}

export function AgentCard({ agent, onClick }: AgentCardProps) {
  const returnPct = agent.return_pct || 0
  const historyValues = agent.history?.map((h) => h.value).filter((v) => v > 0) || []
  const strategyIcon = STRATEGY_ICONS[agent.agent_name?.toLowerCase()] || '🤖'

  return (
    <div className="agent-card" onClick={onClick} style={{ cursor: onClick ? 'pointer' : 'default' }}>
      <div className="agent-card-header">
        <div className="agent-card-avatar">{strategyIcon}</div>
        <div className="agent-card-info">
          <div className="agent-card-name">{agent.agent_name}</div>
          <div className="agent-card-rank">#{agent.rank || '—'} · {agent.trade_count || 0} trades</div>
        </div>
        <div className="agent-card-sparkline">
          <Sparkline data={historyValues} width={80} height={28} />
        </div>
      </div>

      <div className="agent-card-stats">
        <div className="agent-card-stat">
          <span className="stat-label">Portfolio</span>
          <span className="stat-value">${(agent.ending_value || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
        </div>
        <div className="agent-card-stat">
          <span className="stat-label">Return</span>
          <PnLDisplay pnl={returnPct} />
        </div>
        <div className="agent-card-stat">
          <span className="stat-label">Max DD</span>
          <span className="stat-value" style={{ color: 'var(--danger, #ef4444)' }}>
            -{(agent.max_drawdown || 0).toFixed(2)}%
          </span>
        </div>
        <div className="agent-card-stat">
          <span className="stat-label">Risk Score</span>
          <span className="stat-value">{(agent.risk_adjusted_score || 0).toFixed(2)}</span>
        </div>
      </div>
    </div>
  )
}
