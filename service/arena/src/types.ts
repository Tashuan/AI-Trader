// ============================================================
// Arena Type Definitions
// ============================================================

export interface AgentPosition {
  side: string;
  symbol: string;
  pnl: number;
  pnl_pct: number;
  entry_price?: number | null;
  current_price?: number | null;
  stop_loss_price?: number | null;
  take_profit_price?: number | null;
  trailing_sl_pct?: number | null;
  trailing_activation_pct?: number | null;
  peak_favorable_price?: number | null;
  trailing_activated?: boolean;
  quantity?: number | null;
  opened_at?: string | null;
  agent_id?: number;
  agent_name?: string;
  market?: string;
}

export interface AgentLastTrade {
  symbol: string;
  pnl_pct: number;
  action: string;
}

export interface AgentRelationship {
  trust: number;
  dislike: number;
  agrees: number;
  disagrees: number;
}

export interface GoalData {
  goal: {
    target_amount: number;
    deadline: string | null;
    max_loss: number | null;
    description: string;
    status: string;
    created_at: string;
  } | null;
  status: 'active' | 'achieved' | 'max_loss_hit' | 'paused' | 'no_goal';
  can_trade: boolean;
  progress_pct: number;
  current_equity: number;
  starting_equity: number;
  goal_achieved: boolean;
  max_loss_hit: boolean;
}

export interface Agent {
  agent_id: number;
  name: string;
  cash?: number;
  tagline: string;
  bio: string;
  goal: string;
  avatar_seed: string;
  state: string;
  state_detail: string;
  state_symbol: string;
  state_color: string;
  confidence: number;
  confidence_label: string;
  thesis: string;
  personality_quote: string;
  position: AgentPosition | null;
  all_positions: AgentPosition[];
  last_trade: AgentLastTrade | null;
  today_pnl: number;
  today_pnl_pct: number;
  total_profit: number;
  trade_count: number;
  win_rate: number;
  win_streak: number;
  online: boolean;
  bot_running: boolean;
  watchlist: string[];
  quirks: string[];
  relationship_focus: string | null;
  memories: string[];
  relationships: Record<string, AgentRelationship>;
  risk_tolerance: string;
  strategy_type: string;
  last_action?: string;
  last_action_at?: number;
  thoughts: string[];
  goal_data?: GoalData;
}

export interface StockBoySupervisorStatus {
  enabled: boolean;
  actions_enabled: boolean;
  mode: string;
  kill_switch: boolean;
  running: boolean;
  agent_id?: number | null;
  last_cycle_at?: string | null;
  next_cycle_at?: string | null;
  last_heartbeat_at?: string | null;
  last_error?: string | null;
  cycles_run: number;
  controlled_runners: string[];
  thread?: string | null;
  bot_type?: string;
}

export interface StockBoyRunnerHealth {
  runner_key: string;
  agent_name: string;
  agent_id?: number | null;
  running: boolean;
  bot_type: string;
  last_error?: string | null;
  cash: number;
  portfolio_value: number;
  open_positions: number;
  unrealized_pnl: number;
  today_pnl: number;
  active_overrides: number;
  heartbeat_age_seconds?: number | null;
  last_cycle_at?: string | null;
}

export interface StockBoyPosition {
  position_id: number;
  agent_id: number;
  agent_name: string;
  runner_key: string;
  symbol: string;
  market: string;
  side: string;
  quantity: number;
  entry_price: number;
  current_price?: number | null;
  current_price_age_seconds?: number | null;
  unrealized_pnl?: number | null;
  unrealized_pnl_pct?: number | null;
  stop_loss_price?: number | null;
  take_profit_price?: number | null;
  trailing_sl_pct?: number | null;
  trailing_activation_pct?: number | null;
  opened_at?: string | null;
  age_seconds?: number | null;
  missing_protection: boolean;
  stale_price: boolean;
  latest_assessment?: string | null;
}

export interface StockBoyPendingOrder {
  order_id: number;
  agent_id: number;
  agent_name: string;
  runner_key: string;
  symbol: string;
  market: string;
  side: string;
  stop_price: number;
  limit_price?: number | null;
  quantity: number;
  status: string;
  created_at: string;
  expires_at: string;
  age_seconds?: number | null;
  stale: boolean;
}

export interface StockBoyOverride {
  override_id: number;
  runner_key: string;
  field_path: string;
  old_value?: unknown;
  new_value: unknown;
  baseline_version?: string | null;
  rationale: string;
  author: string;
  status: string;
  expires_at?: string | null;
  rolled_back_at?: string | null;
  created_at: string;
}

export interface StockBoyAction {
  action_id: number;
  idempotency_key: string;
  cycle_id?: number | null;
  runner_key: string;
  action_type: string;
  target_position_id?: number | null;
  target_order_id?: number | null;
  parameters: Record<string, unknown>;
  rationale: string;
  policy_rule: string;
  status: string;
  result: Record<string, unknown>;
  error?: string | null;
  requested_at: string;
  executed_at?: string | null;
  created_at: string;
}

export interface StockBoyObservation {
  observation_id: number;
  cycle_id?: number | null;
  runner_key?: string | null;
  severity: string;
  category: string;
  message: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface StockBoyCommentary {
  commentary_id: number;
  kind: string;
  severity: string;
  content: string;
  created_at: string;
}

export interface StockBoySnapshot {
  timestamp: string;
  supervisor: StockBoySupervisorStatus;
  portfolio: {
    total_equity: number;
    total_cash: number;
    total_unrealized_pnl: number;
    total_today_pnl: number;
    gross_exposure: number;
    net_exposure: number;
    open_position_count: number;
    pending_order_count: number;
    controlled_runner_count: number;
    active_override_count: number;
    data_fresh: boolean;
  };
  runners: StockBoyRunnerHealth[];
  positions: StockBoyPosition[];
  pending_orders: StockBoyPendingOrder[];
  overrides: StockBoyOverride[];
  recent_actions: StockBoyAction[];
  recent_observations: StockBoyObservation[];
  recent_commentary: StockBoyCommentary[];
  risk_anomalies: { category: string; severity: string; message: string; runner_key?: string | null; symbol?: string | null; metadata: Record<string, unknown> }[];
  broader_agent_summary: Record<string, unknown>[];
}

export interface MarketData {
  price: number;
  change_pct: number;
  sparkline: number[];
  agents_watching: number;
  bullish_count: number;
  bearish_count: number;
  most_confident_agent: string | null;
  most_confident_direction: string | null;
  agent_positions: { agent: string; side: string; confidence: number }[];
}

export interface Headline {
  headline: string;
  type: string;
  agent: string;
}

export interface CommentaryEntry {
  timestamp: string;
  commentary: string;
  type: string;
  mentioned_agents: string[];
}

export interface TimelineReaction {
  agent: string;
  action: string;
  detail: string;
}

export interface TimelineEvent {
  id: string;
  timestamp: string;
  type: string;
  content: string;
  agent: string;
  reactions: TimelineReaction[];
}

// ============================================================
// Personality Log Types (runner-aware structured events)
// ============================================================

export type RunnerKey = 'scalprunner' | 'blitzrunner' | 'cryptorunner' | string;

export type PersonalityPriority = 'info' | 'action' | 'trade' | 'critical' | 'error';

export type PersonalityKind =
  | 'phase'
  | 'scan'
  | 'decision'
  | 'order'
  | 'entry'
  | 'exit'
  | 'error'
  | 'startup'
  | 'shutdown'
  | 'heartbeat'
  | 'cycle_recap'
  | 'aggregate'
  | 'legacy_activity'
  | 'switch'
  | 'goal'
  | 'portfolio'
  | 'config'
  | 'summary'
  | string;

export interface PersonalityLogEvent {
  event_id: string;
  timestamp: string;
  runner: RunnerKey;
  cycle_id?: string;
  phase?: string;
  kind?: PersonalityKind;
  priority?: PersonalityPriority;
  outcome?: string;
  symbol?: string;
  message: string;
  facts?: Record<string, unknown>;
  agent_name?: string;
  agent_id?: number | null;
  _received_at?: string;
}

export interface PersonalityLogResponse {
  events: PersonalityLogEvent[];
  total: number;
  buffer_size: number;
}

export interface RunnerMeta {
  key: RunnerKey;
  label: string;
  color: string;
}

export const RUNNER_METADATA: RunnerMeta[] = [
  { key: 'scalprunner', label: 'ScalpRunner', color: 'arena-green' },
  { key: 'blitzrunner', label: 'BlitzRunner', color: 'arena-orange' },
  { key: 'cryptorunner', label: 'CryptoRunner', color: 'arena-blue' },
];

export function runnerLabel(key: string): string {
  const meta = RUNNER_METADATA.find(m => m.key === key.toLowerCase());
  return meta?.label ?? key.charAt(0).toUpperCase() + key.slice(1);
}

export function runnerColor(key: string): string {
  const meta = RUNNER_METADATA.find(m => m.key === key.toLowerCase());
  return meta?.color ?? 'arena-purple';
}

// ============================================================
// Normalized Timeline Feed Event (unified shape for the UI)
// ============================================================

export type FeedEventSource = 'personality' | 'legacy' | 'websocket';

export type FeedEventType =
  | 'trade'
  | 'thought'
  | 'strategy'
  | 'discussion'
  | 'reply'
  | 'operation'
  | 'phase'
  | 'scan'
  | 'decision'
  | 'order'
  | 'entry'
  | 'exit'
  | 'error'
  | 'startup'
  | 'shutdown'
  | 'heartbeat'
  | 'cycle_recap'
  | 'aggregate'
  | 'state_change'
  | string;

export interface FeedEvent {
  id: string;
  timestamp: string;
  source: FeedEventSource;
  type: FeedEventType;
  runner?: RunnerKey;
  agent: string;
  agent_id?: number | null;
  title?: string;
  content: string;
  detail?: string;
  symbol?: string;
  market?: string;
  side?: string;
  priority?: PersonalityPriority;
  outcome?: string;
  phase?: string;
  kind?: string;
  cycle_id?: string;
  facts?: Record<string, unknown>;
  pnl?: number;
  pnl_pct?: number;
  price?: number;
  quantity?: number;
  reactions: TimelineReaction[];
}

export interface ArenaFullResponse {
  agents: Agent[];
  markets: Record<string, MarketData>;
  headlines: Headline[];
  commentary: CommentaryEntry[];
  timeline: TimelineEvent[];
  breaking_event: {
    headline: string;
    source: string;
    timestamp: string;
    affected_symbols: string[];
  } | null;
  timestamp: string;
}

export interface WsActivityEvent {
  type: string;
  agent_id: number;
  agent_name: string;
  message_type: string;
  action: string;
  symbol: string;
  market: string;
  title: string;
  content: string;
  side: string;
  signal_type: string;
  price: number;
  quantity: number;
  timestamp: string;
  // state_change fields
  state?: string;
  state_detail?: string;
  state_symbol?: string;
  state_color?: string;
  confidence?: number;
}

export interface PortfolioRiskThresholds {
  max_symbol_pct: number;
  max_sector_pct: number;
  max_unknown_pct: number;
  max_crowding: number;
  max_daily_loss_pct: number;
}

export interface PortfolioRiskSymbolExposure {
  value: number;
  pct: number;
  agents: number;
}

export interface PortfolioRiskSectorExposure {
  value: number;
  pct: number;
}

export interface PortfolioRiskCrowdingEntry {
  agent: string;
  side: string;
}

export interface UserInfo {
  name: string | null;
  role: string;
  is_admin: boolean;
}

export interface PortfolioRiskData {
  total_equity: number;
  starting_equity: number;
  symbol_exposure: Record<string, PortfolioRiskSymbolExposure>;
  sector_exposure: Record<string, PortfolioRiskSectorExposure>;
  crowding: Record<string, PortfolioRiskCrowdingEntry[]>;
  daily_pnl: number;
  daily_pnl_pct: number;
  halted: number;
  halt_reason: string | null;
  thresholds: PortfolioRiskThresholds;
}
