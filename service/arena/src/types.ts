// ============================================================
// Arena Type Definitions
// ============================================================

export interface AgentPosition {
  side: string;
  symbol: string;
  pnl: number;
  pnl_pct: number;
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

export interface Agent {
  agent_id: number;
  name: string;
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
}
