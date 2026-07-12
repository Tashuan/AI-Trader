import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  useAgentList,
  deleteAgent,
  activateAgent,
  deactivateAgent,
  regenerateFiles,
  resetAgentToken,
  resetAgentCash,
  getAgentDetail,
  updateAgent,
  type AgentConfig,
} from '../hooks/useAgentManager'
import { LoadingSpinner, EmptyState, StatCard } from '../components/Common'

const STRATEGY_ICONS: Record<string, string> = {
  news_sentiment: 'N',
  technical_analysis: 'T',
  contrarian: 'C',
  momentum: 'M',
  momentum_scalp: '⚡',
  copy_trader: 'P',
  stat_arb: 'S',
  event_driven: 'E',
  range: 'R',
  custom: '?',
}

export function AgentManagerPage({ token = '' }: { token?: string }) {
  const navigate = useNavigate()
  const { agents, total, loading, error, refetch } = useAgentList(token, 30000)
  const [selectedAgent, setSelectedAgent] = useState<AgentConfig | null>(null)
  const [detail, setDetail] = useState<any>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null)
  const [actionMsg, setActionMsg] = useState<string | null>(null)
  const [filterStatus, setFilterStatus] = useState<string>('')
  const [filterStrategy, setFilterStrategy] = useState<string>('')
  const [launchAgent, setLaunchAgent] = useState<AgentConfig | null>(null)

  const filteredAgents = agents.filter((a) => {
    if (filterStatus && a.status !== filterStatus) return false
    if (filterStrategy && a.strategy_type !== filterStrategy) return false
    return true
  })

  const openDetail = async (agent: AgentConfig) => {
    setSelectedAgent(agent)
    setDetailLoading(true)
    try {
      const d = await getAgentDetail(token, agent.id)
      setDetail(d)
    } catch (e: any) {
      setActionMsg(e.message)
    } finally {
      setDetailLoading(false)
    }
  }

  const closeDetail = () => {
    setSelectedAgent(null)
    setDetail(null)
  }

  const handleAction = async (action: string, agentId: number, ...args: any[]) => {
    try {
      let result
      switch (action) {
        case 'activate': result = await activateAgent(token, agentId); break
        case 'deactivate': result = await deactivateAgent(token, agentId); break
        case 'regenerate': result = await regenerateFiles(token, agentId); break
        case 'resetToken': result = await resetAgentToken(token, agentId); break
        case 'resetCash': result = await resetAgentCash(token, agentId, args[0] || 100000); break
        case 'delete': result = await deleteAgent(token, agentId, args[0] || false); break
      }
      setActionMsg(result?.message || `${action} successful`)
      if (action === 'delete') {
        closeDetail()
      }
      refetch()
      if (selectedAgent?.id === agentId && action !== 'delete') {
        openDetail({ ...selectedAgent, status: action === 'activate' ? 'active' : action === 'deactivate' ? 'inactive' : selectedAgent.status })
      }
      setTimeout(() => setActionMsg(null), 3000)
    } catch (e: any) {
      setActionMsg(e.message)
      setTimeout(() => setActionMsg(null), 5000)
    }
  }

  const handleLaunch = async (agent: AgentConfig) => {
    try {
      await activateAgent(token, agent.id)
      refetch()
    } catch {
      // ignore — status flag is best-effort
    }
    setLaunchAgent(agent)
  }

  if (loading) return <LoadingSpinner />
  if (error) return <EmptyState icon="!" title="Failed to load agents" subtitle={error} />

  if (launchAgent) {
    return (
      <LaunchModal
        agent={launchAgent}
        onClose={() => {
          setLaunchAgent(null)
          if (selectedAgent) {
            openDetail({ ...selectedAgent, status: 'active' })
          }
        }}
      />
    )
  }

  if (selectedAgent) {
    return (
      <AgentDetailPanel
        agent={selectedAgent}
        detail={detail}
        detailLoading={detailLoading}
        token={token}
        onClose={closeDetail}
        onAction={handleAction}
        onLaunch={() => handleLaunch(selectedAgent)}
        confirmDelete={confirmDelete}
        setConfirmDelete={setConfirmDelete}
        actionMsg={actionMsg}
        setActionMsg={setActionMsg}
      />
    )
  }

  return (
    <div className="agent-manager-page">
      <div className="page-header">
        <h2>Agent Manager</h2>
        <span className="page-subtitle">{total} agents registered</span>
      </div>

      <div className="agent-manager-toolbar">
        <div className="agent-manager-filters">
          <select className="manager-filter-select" value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}>
            <option value="">All Status</option>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </select>
          <select className="manager-filter-select" value={filterStrategy} onChange={(e) => setFilterStrategy(e.target.value)}>
            <option value="">All Strategies</option>
            <option value="news_sentiment">News Sentiment</option>
            <option value="technical_analysis">Technical Analysis</option>
            <option value="contrarian">Contrarian</option>
            <option value="momentum">Momentum</option>
            <option value="copy_trader">Copy Trader</option>
            <option value="stat_arb">Stat Arb / Pairs</option>
            <option value="event_driven">Event-Driven</option>
            <option value="range">Range / Grid</option>
            <option value="custom">Custom</option>
          </select>
        </div>
        <button className="manager-btn-create" onClick={() => navigate('/agent-builder')}>
          + Create New Agent
        </button>
      </div>

      {actionMsg && <div className="manager-action-msg">{actionMsg}</div>}

      {filteredAgents.length === 0 ? (
        <EmptyState icon="?" title="No agents found" subtitle="Create your first agent to get started" />
      ) : (
        <div className="manager-agent-grid">
          {filteredAgents.map((agent) => (
            <AgentManagerCard
              key={agent.id}
              agent={agent}
              onClick={() => openDetail(agent)}
              onActivate={() => handleAction('activate', agent.id)}
              onDeactivate={() => handleAction('deactivate', agent.id)}
              onLaunch={() => handleLaunch(agent)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function AgentManagerCard({
  agent,
  onClick,
  onActivate,
  onDeactivate,
  onLaunch,
}: {
  agent: AgentConfig
  onClick: () => void
  onActivate: () => void
  onDeactivate: () => void
  onLaunch: () => void
}) {
  const icon = STRATEGY_ICONS[agent.strategy_type || 'custom'] || '?'
  const isActive = agent.status === 'active'
  const profit = agent.current_profit || 0
  const profitColor = profit >= 0 ? 'var(--success)' : 'var(--error)'

  return (
    <div className="manager-agent-card" onClick={onClick}>
      <div className="manager-card-header">
        <div className="manager-card-avatar">{icon}</div>
        <div className="manager-card-info">
          <div className="manager-card-name">{agent.name}</div>
          <div className="manager-card-strategy">{agent.strategy_type || 'custom'}</div>
        </div>
        <div className={`manager-status-badge ${isActive ? 'active' : 'inactive'}`}>
          {isActive ? 'Active' : 'Inactive'}
        </div>
      </div>

      {agent.tagline && <div className="manager-card-tagline">{agent.tagline}</div>}

      <div className="manager-card-stats">
        <div className="manager-stat">
          <span className="manager-stat-label">Trades</span>
          <span className="manager-stat-value">{agent.total_trades || 0}</span>
        </div>
        <div className="manager-stat">
          <span className="manager-stat-label">Portfolio</span>
          <span className="manager-stat-value">${(agent.current_value || agent.cash || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
        </div>
        <div className="manager-stat">
          <span className="manager-stat-label">P&L</span>
          <span className="manager-stat-value" style={{ color: profitColor }}>
            {profit >= 0 ? '+' : ''}${Math.abs(profit).toLocaleString(undefined, { maximumFractionDigits: 0 })}
          </span>
        </div>
        <div className="manager-stat">
          <span className="manager-stat-label">Positions</span>
          <span className="manager-stat-value">{agent.position_count || 0}</span>
        </div>
      </div>

      <div className="manager-card-actions" onClick={(e) => e.stopPropagation()}>
        <button
          className={`manager-btn-sm ${isActive ? 'deactivate' : 'activate'}`}
          onClick={isActive ? onDeactivate : onActivate}
        >
          {isActive ? 'Deactivate' : 'Activate'}
        </button>
        <button className="manager-btn-sm launch" onClick={onLaunch}>
          Launch in Windsurf
        </button>
        <button className="manager-btn-sm" onClick={onClick}>Details</button>
      </div>
    </div>
  )
}

function AgentDetailPanel({
  agent,
  detail,
  detailLoading,
  token,
  onClose,
  onAction,
  onLaunch,
  confirmDelete,
  setConfirmDelete,
  actionMsg,
  setActionMsg,
}: {
  agent: AgentConfig
  detail: any
  detailLoading: boolean
  token?: string
  onClose: () => void
  onAction: (action: string, agentId: number, ...args: any[]) => void
  onLaunch: () => void
  confirmDelete: number | null
  setConfirmDelete: (id: number | null) => void
  actionMsg: string | null
  setActionMsg: (msg: string | null) => void
}) {
  const [editMode, setEditMode] = useState(false)
  const [editValues, setEditValues] = useState<Record<string, any>>({})
  const [editSaving, setEditSaving] = useState(false)

  useEffect(() => {
    if (detail) {
      setEditValues({
        tagline: detail.tagline || '',
        bio: detail.bio || '',
        risk_tolerance: detail.risk_tolerance || 'moderate',
        position_sizing: detail.position_sizing || 'medium',
        hold_period: detail.hold_period || 'swing',
        max_positions: detail.max_positions || 6,
        confidence_threshold: detail.confidence_threshold || 0.60,
        fomo_resistance: detail.fomo_resistance || 0.70,
        loss_aversion: detail.loss_aversion || 0.70,
        conviction_multiplier: detail.conviction_multiplier || 1.2,
        voice: detail.voice || '',
        emoji_frequency: detail.emoji_frequency || 'rare',
        publishes_reasoning: detail.publishes_reasoning ?? true,
        trash_talk: detail.trash_talk ?? false,
        strategy_type: detail.strategy_type || 'custom',
        status: detail.status || 'inactive',
        poll_interval: detail.poll_interval || 300,
      })
    }
  }, [detail])

  const saveEdit = async () => {
    setEditSaving(true)
    try {
      await updateAgent(token, agent.id, { ...editValues, generate_files: true })
      setEditMode(false)
      onAction('refresh', agent.id)
    } catch (e: any) {
      setActionMsg(e.message)
    } finally {
      setEditSaving(false)
    }
  }

  const profit = detail?.current_profit || 0

  return (
    <div className="agent-detail-panel">
      <div className="detail-header">
        <button className="detail-back-btn" onClick={onClose}>{'< Back to List'}</button>
        <h2>{agent.name}</h2>
        <div className={`detail-status-badge ${agent.status === 'active' ? 'active' : 'inactive'}`}>
          {agent.status || 'inactive'}
        </div>
      </div>

      {actionMsg && <div className="manager-action-msg">{actionMsg}</div>}

      <div className="detail-stats-grid">
        <StatCard label="Portfolio Value" value={`$${(detail?.current_value || agent.cash || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`} />
        <StatCard label="Cash" value={`$${(detail?.current_cash || agent.cash || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`} />
        <StatCard label="P&L" value={`${profit >= 0 ? '+' : ''}$${Math.abs(profit).toLocaleString(undefined, { maximumFractionDigits: 0 })}`} trend={profit >= 0 ? 'up' : 'down'} />
        <StatCard label="Total Trades" value={detail?.total_trades || 0} />
        <StatCard label="Positions" value={detail?.position_count || 0} />
        <StatCard label="Max Drawdown" value={`$${(detail?.max_drawdown || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`} trend="down" />
        <StatCard label="Followers" value={detail?.follower_count || 0} />
        <StatCard label="Signals" value={detail?.total_signals || 0} />
      </div>

      <div className="detail-actions">
        {agent.status === 'active' ? (
          <button className="detail-btn" onClick={() => onAction('deactivate', agent.id)}>Deactivate</button>
        ) : (
          <button className="detail-btn activate" onClick={() => onAction('activate', agent.id)}>Activate</button>
        )}
        <button className="detail-btn launch" onClick={onLaunch}>Launch in Windsurf</button>
        <button className="detail-btn" onClick={() => onAction('regenerate', agent.id)}>Regenerate Files</button>
        <button className="detail-btn" onClick={() => onAction('resetToken', agent.id)}>Reset Token</button>
        <button className="detail-btn" onClick={() => onAction('resetCash', agent.id)}>Reset Cash</button>
        <button className="detail-btn" onClick={() => setEditMode(!editMode)}>
          {editMode ? 'Cancel Edit' : 'Edit Config'}
        </button>
        {confirmDelete === agent.id ? (
          <>
            <button className="detail-btn danger" onClick={() => onAction('delete', agent.id, true)}>Confirm Delete</button>
            <button className="detail-btn" onClick={() => setConfirmDelete(null)}>Cancel</button>
          </>
        ) : (
          <button className="detail-btn danger" onClick={() => setConfirmDelete(agent.id)}>Delete Agent</button>
        )}
      </div>

      {detailLoading && <LoadingSpinner />}

      {editMode && !detailLoading && (
        <div className="detail-edit-form">
          <h3>Edit Configuration</h3>
          <div className="builder-row">
            <div className="builder-field">
              <label className="builder-label">Tagline</label>
              <input className="builder-input" value={editValues.tagline || ''} onChange={(e) => setEditValues({ ...editValues, tagline: e.target.value })} />
            </div>
            <div className="builder-field">
              <label className="builder-label">Strategy Type</label>
              <select className="builder-select" value={editValues.strategy_type || ''} onChange={(e) => setEditValues({ ...editValues, strategy_type: e.target.value })}>
                <option value="news_sentiment">News Sentiment</option>
                <option value="technical_analysis">Technical Analysis</option>
                <option value="contrarian">Contrarian</option>
                <option value="momentum">Momentum</option>
                <option value="copy_trader">Copy Trader</option>
                <option value="stat_arb">Stat Arb / Pairs</option>
                <option value="event_driven">Event-Driven</option>
                <option value="range">Range / Grid</option>
                <option value="custom">Custom</option>
              </select>
            </div>
          </div>

          <label className="builder-label">Bio</label>
          <textarea className="builder-textarea" value={editValues.bio || ''} onChange={(e) => setEditValues({ ...editValues, bio: e.target.value })} rows={3} />

          <div className="builder-row">
            <div className="builder-field">
              <label className="builder-label">Risk Tolerance</label>
              <select className="builder-select" value={editValues.risk_tolerance || ''} onChange={(e) => setEditValues({ ...editValues, risk_tolerance: e.target.value })}>
                <option value="conservative">conservative</option>
                <option value="moderate">moderate</option>
                <option value="aggressive">aggressive</option>
                <option value="degen">degen</option>
              </select>
            </div>
            <div className="builder-field">
              <label className="builder-label">Position Sizing</label>
              <select className="builder-select" value={editValues.position_sizing || ''} onChange={(e) => setEditValues({ ...editValues, position_sizing: e.target.value })}>
                <option value="small">small</option>
                <option value="medium">medium</option>
                <option value="large">large</option>
                <option value="yolo">yolo</option>
              </select>
            </div>
            <div className="builder-field">
              <label className="builder-label">Hold Period</label>
              <select className="builder-select" value={editValues.hold_period || ''} onChange={(e) => setEditValues({ ...editValues, hold_period: e.target.value })}>
                <option value="scalp">scalp</option>
                <option value="swing">swing</option>
                <option value="position">position</option>
                <option value="long-term">long-term</option>
              </select>
            </div>
            <div className="builder-field">
              <label className="builder-label">Max Positions</label>
              <input type="number" className="builder-input" value={editValues.max_positions || 6} onChange={(e) => setEditValues({ ...editValues, max_positions: parseInt(e.target.value) || 1 })} min={1} max={50} />
            </div>
          </div>

          <div className="builder-row">
            <div className="builder-field">
              <label className="builder-label">Confidence ({(editValues.confidence_threshold || 0).toFixed(2)})</label>
              <input type="range" min="0" max="1" step="0.05" value={editValues.confidence_threshold || 0} onChange={(e) => setEditValues({ ...editValues, confidence_threshold: parseFloat(e.target.value) })} />
            </div>
            <div className="builder-field">
              <label className="builder-label">FOMO Resistance ({(editValues.fomo_resistance || 0).toFixed(2)})</label>
              <input type="range" min="0" max="1" step="0.05" value={editValues.fomo_resistance || 0} onChange={(e) => setEditValues({ ...editValues, fomo_resistance: parseFloat(e.target.value) })} />
            </div>
          </div>

          <div className="builder-row">
            <div className="builder-field">
              <label className="builder-label">Loss Aversion ({(editValues.loss_aversion || 0).toFixed(2)})</label>
              <input type="range" min="0" max="1" step="0.05" value={editValues.loss_aversion || 0} onChange={(e) => setEditValues({ ...editValues, loss_aversion: parseFloat(e.target.value) })} />
            </div>
            <div className="builder-field">
              <label className="builder-label">Conviction ({(editValues.conviction_multiplier || 1).toFixed(1)})</label>
              <input type="range" min="0.5" max="3" step="0.1" value={editValues.conviction_multiplier || 1} onChange={(e) => setEditValues({ ...editValues, conviction_multiplier: parseFloat(e.target.value) })} />
            </div>
          </div>

          <div className="builder-checkbox-row">
            <label className="builder-checkbox">
              <input type="checkbox" checked={editValues.publishes_reasoning ?? true} onChange={(e) => setEditValues({ ...editValues, publishes_reasoning: e.target.checked })} />
              <span>Publishes reasoning</span>
            </label>
            <label className="builder-checkbox">
              <input type="checkbox" checked={editValues.trash_talk ?? false} onChange={(e) => setEditValues({ ...editValues, trash_talk: e.target.checked })} />
              <span>Trash talk</span>
            </label>
          </div>

          <button className="builder-btn-primary" onClick={saveEdit} disabled={editSaving}>
            {editSaving ? 'Saving...' : 'Save Changes'}
          </button>
        </div>
      )}

      {!editMode && !detailLoading && detail && (
        <>
          <div className="detail-section">
            <h3>Identity</h3>
            <div className="detail-info-grid">
              <div className="detail-info-item"><span>Name</span><strong>{agent.name}</strong></div>
              <div className="detail-info-item"><span>Email</span><strong>{agent.email || '-'}</strong></div>
              <div className="detail-info-item"><span>Tagline</span><strong>{detail.tagline || '-'}</strong></div>
              <div className="detail-info-item"><span>Strategy</span><strong>{detail.strategy_type || 'custom'}</strong></div>
              <div className="detail-info-item"><span>Voice</span><strong>{detail.voice || '-'}</strong></div>
              <div className="detail-info-item"><span>Risk</span><strong>{detail.risk_tolerance}</strong></div>
              <div className="detail-info-item"><span>Hold Period</span><strong>{detail.hold_period}</strong></div>
              <div className="detail-info-item"><span>Max Positions</span><strong>{detail.max_positions}</strong></div>
            </div>
            {detail.bio && <div className="detail-bio">{detail.bio}</div>}
            {detail.watchlist && detail.watchlist.length > 0 && (
              <div className="detail-watchlist">
                <span className="detail-watchlist-label">Watchlist:</span>
                {detail.watchlist.map((w: string) => <span key={w} className="tag-chip">{w}</span>)}
              </div>
            )}
            {detail.quirks && detail.quirks.length > 0 && (
              <div className="detail-quirks">
                <span className="detail-quirks-label">Quirks:</span>
                {detail.quirks.map((q: string, idx: number) => <div key={idx} className="detail-quirk-item">- {q}</div>)}
              </div>
            )}
          </div>

          <div className="detail-section">
            <h3>Instruction File</h3>
            <div className="detail-info-item">
              <span>Status</span>
              <strong>{detail.instruction_file_exists ? 'Generated' : 'Not found'}</strong>
            </div>
            {detail.instruction_file_path && (
              <div className="detail-info-item"><span>Path</span><strong>{detail.instruction_file_path}</strong></div>
            )}
          </div>

          {detail.positions && detail.positions.length > 0 && (
            <div className="detail-section">
              <h3>Current Positions ({detail.positions.length})</h3>
              <div className="detail-table-wrap">
                <table className="detail-table">
                  <thead>
                    <tr>
                      <th>Symbol</th>
                      <th>Market</th>
                      <th>Side</th>
                      <th>Qty</th>
                      <th>Entry</th>
                      <th>Current</th>
                      <th>Opened</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.positions.map((pos: any, idx: number) => (
                      <tr key={idx}>
                        <td>{pos.symbol}</td>
                        <td>{pos.market}</td>
                        <td>{pos.side}</td>
                        <td>{pos.quantity?.toFixed(4)}</td>
                        <td>${pos.entry_price?.toFixed(2)}</td>
                        <td>${pos.current_price?.toFixed(2) || '-'}</td>
                        <td>{pos.opened_at?.substring(0, 10)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {detail.recent_trades && detail.recent_trades.length > 0 && (
            <div className="detail-section">
              <h3>Recent Trades ({detail.recent_trades.length})</h3>
              <div className="detail-table-wrap">
                <table className="detail-table">
                  <thead>
                    <tr>
                      <th>Symbol</th>
                      <th>Side</th>
                      <th>Price</th>
                      <th>Qty</th>
                      <th>Executed</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.recent_trades.slice(0, 20).map((trade: any, idx: number) => (
                      <tr key={idx}>
                        <td>{trade.symbol}</td>
                        <td>{trade.side}</td>
                        <td>${trade.entry_price?.toFixed(2)}</td>
                        <td>{trade.quantity?.toFixed(4)}</td>
                        <td>{trade.executed_at?.substring(0, 16)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function LaunchModal({ agent, onClose }: { agent: AgentConfig; onClose: () => void }) {
  const [copied, setCopied] = useState(false)
  const instructionFile = `/Users/tashuanspence/Development/ai-trader/agents/AGENT_INSTRUCTIONS_${agent.name}.md`
  const launchPrompt = `Read the file at ${instructionFile} and follow all instructions to start running as the ${agent.name} agent. Begin your first cycle immediately.`

  const copyToClipboard = () => {
    navigator.clipboard.writeText(launchPrompt).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <div className="launch-modal-overlay" onClick={onClose}>
      <div className="launch-modal" onClick={(e) => e.stopPropagation()}>
        <div className="launch-modal-header">
          <h2>Launch {agent.name} in Windsurf</h2>
          <button className="launch-modal-close" onClick={onClose}>x</button>
        </div>

        <div className="launch-modal-body">
          <div className="launch-step">
            <div className="launch-step-num">1</div>
            <div className="launch-step-content">
              <strong>Open a new Cascade conversation</strong>
              <p>In Windsurf, open a new Cascade chat tab (Cmd+Shift+C or the + button in the Cascade panel).</p>
            </div>
          </div>

          <div className="launch-step">
            <div className="launch-step-num">2</div>
            <div className="launch-step-content">
              <strong>Copy and paste this prompt</strong>
              <p>The agent will read its instruction file and start running cycles automatically.</p>
              <div className="launch-prompt-box">
                <code>{launchPrompt}</code>
                <button className="launch-copy-btn" onClick={copyToClipboard}>
                  {copied ? 'Copied!' : 'Copy'}
                </button>
              </div>
            </div>
          </div>

          <div className="launch-step">
            <div className="launch-step-num">3</div>
            <div className="launch-step-content">
              <strong>Press Enter</strong>
              <p>The agent will register on the platform, run its first analysis cycle, and continue running every 5 minutes. Keep the Cascade tab open.</p>
            </div>
          </div>

          <div className="launch-info-box">
            <div className="launch-info-row">
              <span>Instruction file:</span>
              <code>{instructionFile}</code>
            </div>
            <div className="launch-info-row">
              <span>Platform API:</span>
              <code>http://localhost:8000/api</code>
            </div>
            <div className="launch-info-row">
              <span>Status:</span>
              <span className="launch-status-active">Marked as Active</span>
            </div>
          </div>

          <div className="launch-modal-footer">
            <p className="launch-tip">Tip: Each agent runs in its own Cascade tab. To run multiple agents simultaneously, open multiple tabs and paste each agent's prompt.</p>
            <button className="launch-done-btn" onClick={onClose}>Done</button>
          </div>
        </div>
      </div>
    </div>
  )
}
