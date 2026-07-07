import { useState, useEffect, useCallback } from 'react'

const API_BASE = '/api'

export interface AgentConfig {
  id: number
  name: string
  email?: string
  cash: number
  points: number
  reputation_score: number
  created_at: string
  tagline?: string
  bio?: string
  risk_tolerance?: string
  position_sizing?: string
  hold_period?: string
  max_positions?: number
  strategy_type?: string
  watchlist?: string[]
  quirks?: string[]
  status?: string
  auto_start?: boolean
  poll_interval?: number
  api_base?: string
  voice?: string
  emoji_frequency?: string
  publishes_reasoning?: boolean
  trash_talk?: boolean
  confidence_threshold?: number
  fomo_resistance?: number
  loss_aversion?: number
  conviction_multiplier?: number
  initial_cash?: number
  total_trades?: number
  total_signals?: number
  total_strategies?: number
  total_discussions?: number
  total_replies?: number
  position_count?: number
  position_value?: number
  current_value?: number
  current_cash?: number
  current_profit?: number
  max_drawdown?: number
  last_trade_at?: string
  follower_count?: number
  following_count?: number
}

export interface StrategyTemplate {
  label: string
  description: string
  strategy_file: string | null
  default_watchlist: string[]
  default_voice: string
  default_risk: string
  default_hold: string
  default_confidence: number
}

export interface AgentFormData {
  name: string
  email?: string
  password?: string
  tagline: string
  bio: string
  risk_tolerance: string
  position_sizing: string
  hold_period: string
  max_positions: number
  confidence_threshold: number
  fomo_resistance: number
  loss_aversion: number
  conviction_multiplier: number
  voice: string
  emoji_frequency: string
  publishes_reasoning: boolean
  trash_talk: boolean
  strategy_type: string
  watchlist: string[]
  quirks: string[]
  initial_cash: number
  auto_start: boolean
  poll_interval: number
  api_base: string
  generate_files: boolean
}

function authHeaders(token?: string): Record<string, string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`
  return headers
}

export function useAgentList(token: string | null, pollInterval?: number) {
  const [agents, setAgents] = useState<AgentConfig[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchAgents = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/agents/manage/list?limit=100`, {
        headers: authHeaders(token || undefined),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setAgents(data.agents || [])
      setTotal(data.total || 0)
      setError(null)
    } catch (e: any) {
      setError(e.message || 'Failed to fetch agents')
    } finally {
      setLoading(false)
    }
  }, [token])

  useEffect(() => {
    fetchAgents()
    if (pollInterval && pollInterval > 0) {
      const interval = setInterval(fetchAgents, pollInterval)
      return () => clearInterval(interval)
    }
  }, [fetchAgents, pollInterval])

  return { agents, total, loading, error, refetch: fetchAgents }
}

export function useStrategyTemplates() {
  const [templates, setTemplates] = useState<Record<string, StrategyTemplate>>({})
  const [options, setOptions] = useState<{
    risk_tolerances: string[]
    position_sizings: string[]
    hold_periods: string[]
    emoji_frequencies: string[]
  }>({ risk_tolerances: [], position_sizings: [], hold_periods: [], emoji_frequencies: [] })
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      fetch(`${API_BASE}/agents/manage/templates`).then((r) => r.json()),
      fetch(`${API_BASE}/agents/manage/options`).then((r) => r.json()),
    ]).then(([tplData, optData]) => {
      setTemplates(tplData.templates || {})
      setOptions(optData)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  return { templates, options, loading }
}

export async function createAgent(token?: string, data?: AgentFormData): Promise<any> {
  const res = await fetch(`${API_BASE}/agents/manage/create`, {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify(data),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
    throw new Error(err.detail || 'Failed to create agent')
  }
  return res.json()
}

export async function updateAgent(token?: string, agentId?: number, data?: Partial<AgentFormData>): Promise<any> {
  const res = await fetch(`${API_BASE}/agents/manage/${agentId}`, {
    method: 'PUT',
    headers: authHeaders(token),
    body: JSON.stringify(data),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
    throw new Error(err.detail || 'Failed to update agent')
  }
  return res.json()
}

export async function deleteAgent(token?: string, agentId?: number, deleteFiles: boolean = false): Promise<any> {
  const res = await fetch(`${API_BASE}/agents/manage/${agentId}?delete_files=${deleteFiles}`, {
    method: 'DELETE',
    headers: authHeaders(token),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
    throw new Error(err.detail || 'Failed to delete agent')
  }
  return res.json()
}

export async function activateAgent(token?: string, agentId?: number): Promise<any> {
  const res = await fetch(`${API_BASE}/agents/manage/${agentId}/activate`, {
    method: 'POST',
    headers: authHeaders(token),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function deactivateAgent(token?: string, agentId?: number): Promise<any> {
  const res = await fetch(`${API_BASE}/agents/manage/${agentId}/deactivate`, {
    method: 'POST',
    headers: authHeaders(token),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function regenerateFiles(token?: string, agentId?: number): Promise<any> {
  const res = await fetch(`${API_BASE}/agents/manage/${agentId}/regenerate-files`, {
    method: 'POST',
    headers: authHeaders(token),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function resetAgentToken(token?: string, agentId?: number): Promise<any> {
  const res = await fetch(`${API_BASE}/agents/manage/${agentId}/reset-token`, {
    method: 'POST',
    headers: authHeaders(token),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function resetAgentCash(token?: string, agentId?: number, amount: number = 100000): Promise<any> {
  const res = await fetch(`${API_BASE}/agents/manage/${agentId}/reset-cash?amount=${amount}`, {
    method: 'POST',
    headers: authHeaders(token),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function getAgentDetail(token?: string, agentId?: number): Promise<any> {
  const res = await fetch(`${API_BASE}/agents/manage/${agentId}`, {
    headers: authHeaders(token),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function getAgentStats(token?: string, agentId?: number): Promise<any> {
  const res = await fetch(`${API_BASE}/agents/manage/${agentId}/stats`, {
    headers: authHeaders(token),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function getAgentTrades(token?: string, agentId?: number, limit: number = 50): Promise<any> {
  const res = await fetch(`${API_BASE}/agents/manage/${agentId}/trades?limit=${limit}`, {
    headers: authHeaders(token),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}
