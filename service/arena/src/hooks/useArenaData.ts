import { useState, useEffect, useRef, useCallback } from 'react';
import type { ArenaFullResponse, WsActivityEvent, PortfolioRiskData, UserInfo } from '../types';
import { playSoundForEvent } from '../utils/sounds';

const API_BASE = '/api';
const WS_URL = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws/activity`;
const FULL_REFRESH_INTERVAL = 30000;
const PORTFOLIO_RISK_INTERVAL = 30000;
const FAST_RETRY_INTERVAL = 5000;
const WS_RECONNECT_BASE = 1000;
const WS_RECONNECT_MAX = 30000;
const WS_EVENT_BUFFER_MAX = 50;

export function useArenaData() {
  const [data, setData] = useState<ArenaFullResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [mentionedAgent, setMentionedAgent] = useState<string | null>(null);
  const [portfolioRisk, setPortfolioRisk] = useState<PortfolioRiskData | null>(null);
  const [portfolioRiskLastUpdated, setPortfolioRiskLastUpdated] = useState<number | null>(null);
  const [userInfo, setUserInfo] = useState<UserInfo | null>(null);
  const [wsEvents, setWsEvents] = useState<WsActivityEvent[]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const mentionedTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchFull = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}/arena/full`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const json = await resp.json();
      setData(json);
      setError(null);
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to fetch arena data';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchPortfolioRisk = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}/arena/portfolio-risk`);
      if (!resp.ok) return;
      const json = await resp.json();
      setPortfolioRisk(json);
      setPortfolioRiskLastUpdated(Date.now());
    } catch {
      // silent fail
    }
  }, []);

  const fetchUserInfo = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}/arena/me`, {
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('auth_token') || ''}`,
        },
      });
      if (!resp.ok) return;
      const json = await resp.json();
      setUserInfo(json);
    } catch {
      // silent fail
    }
  }, []);

  // Initial load + adaptive polling (fast retry on error, normal interval on success)
  useEffect(() => {
    fetchFull();
    fetchPortfolioRisk();
    fetchUserInfo();

    let fullTimer: ReturnType<typeof setTimeout>;
    let riskTimer: ReturnType<typeof setTimeout>;

    const scheduleFull = () => {
      const interval = error ? FAST_RETRY_INTERVAL : FULL_REFRESH_INTERVAL;
      fullTimer = setTimeout(async () => {
        await fetchFull();
        scheduleFull();
      }, interval);
    };

    const scheduleRisk = () => {
      riskTimer = setTimeout(async () => {
        await fetchPortfolioRisk();
        scheduleRisk();
      }, PORTFOLIO_RISK_INTERVAL);
    };

    scheduleFull();
    scheduleRisk();

    return () => {
      clearTimeout(fullTimer);
      clearTimeout(riskTimer);
    };
  }, [fetchFull, fetchPortfolioRisk, error]);

  // WebSocket for real-time events with exponential backoff
  useEffect(() => {
    let reconnectTimer: ReturnType<typeof setTimeout>;
    let backoff = WS_RECONNECT_BASE;

    const connectWs = () => {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        backoff = WS_RECONNECT_BASE;
      };

      ws.onmessage = (event) => {
        try {
          const msg: WsActivityEvent = JSON.parse(event.data);
          handleWsEvent(msg);
        } catch {
          // ignore parse errors
        }
      };

      ws.onclose = () => {
        reconnectTimer = setTimeout(connectWs, backoff);
        backoff = Math.min(backoff * 2, WS_RECONNECT_MAX);
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    connectWs();

    return () => {
      clearTimeout(reconnectTimer);
      wsRef.current?.close();
    };
  }, []);

  const handleWsEvent = useCallback((msg: WsActivityEvent) => {
    // Play sound for major events
    playSoundForEvent(msg);

    // Buffer raw ws events for the timeline page
    setWsEvents(prev => [msg, ...prev].slice(0, WS_EVENT_BUFFER_MAX));

    // Update agent state and card data based on WebSocket event
    setData(prev => {
      if (!prev) return prev;

      // Check for mentions in discussion/reply content
      const content = msg.content || msg.title || '';
      if (msg.message_type === 'discussion' || msg.message_type === 'reply') {
        const mentioned = prev.agents.find(a =>
          content.toLowerCase().includes(a.name.toLowerCase()) &&
          a.name.toLowerCase() !== (msg.agent_name || '').toLowerCase()
        );
        if (mentioned) {
          setMentionedAgent(mentioned.name);
          if (mentionedTimeoutRef.current) clearTimeout(mentionedTimeoutRef.current);
          mentionedTimeoutRef.current = setTimeout(() => setMentionedAgent(null), 2000);
        }
      }

      // Build last-action text for card flash
      let lastAction = '';
      if (msg.message_type === 'state_change') {
        // no last action text for state changes — the state itself updates
      } else if (msg.message_type === 'operation') {
        const action = msg.action || msg.signal_type || msg.side || 'traded';
        lastAction = `${action.toUpperCase()} ${msg.symbol || ''}`.trim();
      } else if (msg.message_type === 'strategy') {
        lastAction = `Published: ${msg.title || 'analysis'}`;
      } else if (msg.message_type === 'discussion') {
        lastAction = `Discussion: ${msg.title || 'post'}`;
      } else if (msg.message_type === 'reply') {
        lastAction = `Replied to signal`;
      }

      // Update agent data in real-time
      const nowTs = Date.now();
      const agents = prev.agents.map(a => {
        if (a.agent_id !== msg.agent_id) return a;

        // Handle thought messages — prepend to thoughts array
        if (msg.message_type === 'thought' && msg.content) {
          return {
            ...a,
            thoughts: [msg.content, ...a.thoughts].slice(0, 5),
            online: true,
          };
        }

        if (msg.message_type === 'state_change') {
          return {
            ...a,
            state: msg.state || a.state,
            state_detail: msg.state_detail || a.state_detail,
            state_symbol: msg.state_symbol || a.state_symbol,
            state_color: msg.state_color || a.state_color,
            confidence: msg.confidence ?? a.confidence,
            confidence_label: msg.confidence != null
              ? (msg.confidence >= 0.9 ? 'All In' : msg.confidence >= 0.75 ? 'High Conviction' : msg.confidence >= 0.5 ? 'Confident' : msg.confidence >= 0.3 ? 'Interested' : 'Unsure')
              : a.confidence_label,
          };
        }

        if (lastAction) {
          return {
            ...a,
            last_action: lastAction,
            last_action_at: nowTs,
          };
        }

        return a;
      });

      // Update timeline with new event
      const newTimelineEvent = {
        id: `ws_${Date.now()}`,
        timestamp: msg.timestamp || new Date().toISOString(),
        type: msg.message_type === 'operation' ? 'trade' : msg.message_type || 'event',
        content: formatWsEvent(msg),
        agent: msg.agent_name || 'Unknown',
        reactions: [],
      };

      return {
        ...prev,
        agents,
        timeline: [newTimelineEvent, ...prev.timeline].slice(0, 20),
      };
    });
  }, []);

  return { data, loading, error, mentionedAgent, portfolioRisk, portfolioRiskLastUpdated, userInfo, wsEvents, fetchPortfolioRisk };
}

function formatWsEvent(msg: WsActivityEvent): string {
  const name = msg.agent_name || 'Unknown';
  if (msg.message_type === 'operation') {
    const action = msg.action || msg.signal_type || 'traded';
    const symbol = msg.symbol || '';
    return `${name} ${action} ${symbol}`.trim();
  }
  if (msg.message_type === 'strategy') {
    return `${name}: ${msg.title || 'Published analysis'}`;
  }
  if (msg.message_type === 'discussion') {
    return `${name}: ${msg.title || 'Started a discussion'}`;
  }
  if (msg.message_type === 'reply') {
    return `${name} replied: ${(msg.content || '').slice(0, 100)}`;
  }
  if (msg.message_type === 'thought') {
    return msg.content || '';
  }
  return `${name}: ${msg.title || msg.content || 'Activity'}`;
}
