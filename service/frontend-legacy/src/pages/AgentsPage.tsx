import { useProfitHistory } from '../hooks/useAgents'
import { AgentCard } from '../components/AgentCard'
import { LoadingSpinner, EmptyState } from '../components/Common'

export function AgentsPage({ onSelectAgent }: { onSelectAgent?: (agentId: number) => void }) {
  const { data, loading, error } = useProfitHistory(50, 30, 30000)

  if (loading) return <LoadingSpinner />
  if (error) return <EmptyState icon="⚠️" title="Failed to load agents" subtitle={error} />
  if (!data?.top_agents?.length) return <EmptyState icon="🤖" title="No agents yet" subtitle="Register agents to see them here" />

  return (
    <div className="agents-page">
      <div className="page-header">
        <h2>Agent Profiles</h2>
        <span className="page-subtitle">{data.total} agents registered</span>
      </div>
      <div className="agent-grid">
        {data.top_agents.map((agent) => (
          <AgentCard
            key={agent.agent_id}
            agent={agent}
            onClick={() => onSelectAgent?.(agent.agent_id)}
          />
        ))}
      </div>
    </div>
  )
}
