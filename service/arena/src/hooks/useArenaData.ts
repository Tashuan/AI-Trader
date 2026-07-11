import { useState, useEffect, useRef, useCallback } from 'react';
import type { ArenaFullResponse, WsActivityEvent, CommentaryEntry } from '../types';

const API_BASE = '/api';
const WS_URL = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws/activity`;
const FULL_REFRESH_INTERVAL = 30000;
const COMMENTARY_REFRESH_INTERVAL = 45000;

export function useArenaData() {
  const [data, setData] = useState<ArenaFullResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [commentary, setCommentary] = useState<CommentaryEntry[]>([]);
  const [mentionedAgent, setMentionedAgent] = useState<string | null>(null);
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
      setError(e instanceof Error ? e.message : 'Failed to fetch arena data');
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchCommentary = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}/arena/narrative/commentary`);
      if (!resp.ok) return;
      const json = await resp.json();
      if (json.commentary && json.commentary.length > 0) {
        setCommentary(prev => {
          const newEntries = json.commentary.filter(
            (entry: CommentaryEntry) => !prev.some(p => p.commentary === entry.commentary)
          );
          return [...newEntries, ...prev].slice(0, 20);
        });
      }
    } catch {
      // silent fail
    }
  }, []);

  // Initial load + periodic refresh
  useEffect(() => {
    fetchFull();
    fetchCommentary();

    const fullInterval = setInterval(fetchFull, FULL_REFRESH_INTERVAL);
    const commentaryInterval = setInterval(fetchCommentary, COMMENTARY_REFRESH_INTERVAL);

    return () => {
      clearInterval(fullInterval);
      clearInterval(commentaryInterval);
    };
  }, [fetchFull, fetchCommentary]);

  // WebSocket for real-time events
  useEffect(() => {
    let reconnectTimer: ReturnType<typeof setTimeout>;

    const connectWs = () => {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onmessage = (event) => {
        try {
          const msg: WsActivityEvent = JSON.parse(event.data);
          handleWsEvent(msg);
        } catch {
          // ignore parse errors
        }
      };

      ws.onclose = () => {
        reconnectTimer = setTimeout(connectWs, 3000);
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

  return { data, loading, error, commentary, mentionedAgent };
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
