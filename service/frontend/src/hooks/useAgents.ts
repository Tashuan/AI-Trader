import { useApi } from './useApi'

export interface AgentLeaderboardEntry {
  agent_id: number
  agent_name: string
  return_pct: number
  max_drawdown: number
  risk_adjusted_score: number
  final_score: number
  trade_count: number
  rank: number
  cash: number
  ending_value: number
  history?: { timestamp: string; value: number }[]
}

export function useProfitHistory(limit = 20, days = 30, pollInterval?: number) {
  return useApi<{ top_agents: AgentLeaderboardEntry[]; total: number }>(
    `/profit/history?limit=${limit}&days=${days}&include_history=true`,
    undefined,
    pollInterval
  )
}

export function useAgentCount() {
  return useApi<{ count: number }>('/claw/agents/count', undefined, 60000)
}
