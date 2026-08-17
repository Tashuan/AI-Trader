import { useState, useEffect, useRef, useCallback } from 'react';
import type {
  FeedEvent,
  PersonalityLogEvent,
  PersonalityLogResponse,
  TimelineEvent,
  WsActivityEvent,
} from '../types';

const API_BASE = '/api';
const POLL_INTERVAL = 5000;
const MAX_EVENTS = 200;

/**
 * Fetch personality-log events from the server and normalize them
 * into the unified FeedEvent shape.  Also accepts legacy TimelineEvent[]
 * and WebSocket events so the page has a single rendering path.
 */
export function useTimelineData(
  legacyTimeline: TimelineEvent[],
  wsEvents: WsActivityEvent[],
) {
  const [personalityEvents, setPersonalityEvents] = useState<PersonalityLogEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const seenIdsRef = useRef<Set<string>>(new Set());

  const fetchPersonalityLog = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}/arena/personality-log?limit=200`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const json: PersonalityLogResponse = await resp.json();
      setPersonalityEvents(json.events || []);
      setError(null);
      setLastUpdated(Date.now());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch personality log');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPersonalityLog();
    const interval = setInterval(fetchPersonalityLog, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [fetchPersonalityLog]);

  // Normalize personality-log events → FeedEvent
  const personalityFeed: FeedEvent[] = personalityEvents.map(e => ({
    id: e.event_id,
    timestamp: e.timestamp,
    source: 'personality' as const,
    type: (e.kind || e.phase || 'event') as FeedEvent['type'],
    runner: e.runner,
    agent: e.agent_name || runnerLabelForEvent(e),
    agent_id: e.agent_id ?? null,
    content: e.message || '',
    detail: e.outcome ? `outcome: ${e.outcome}` : undefined,
    symbol: e.symbol || undefined,
    priority: e.priority,
    outcome: e.outcome,
    phase: e.phase,
    kind: e.kind,
    cycle_id: e.cycle_id,
    facts: e.facts,
    reactions: [],
  }));

  // Normalize legacy timeline events → FeedEvent
  const legacyFeed: FeedEvent[] = legacyTimeline.map(e => ({
    id: e.id,
    timestamp: e.timestamp,
    source: 'legacy' as const,
    type: e.type as FeedEvent['type'],
    agent: e.agent || 'Unknown',
    content: e.content,
    reactions: e.reactions || [],
  }));

  // Normalize WebSocket events → FeedEvent
  const wsFeed: FeedEvent[] = wsEvents.map((msg, i) => ({
    id: `ws_${msg.timestamp}_${i}`,
    timestamp: msg.timestamp,
    source: 'websocket' as const,
    type: (msg.message_type === 'operation' ? 'trade' : msg.message_type || 'event') as FeedEvent['type'],
    agent: msg.agent_name || 'Unknown',
    agent_id: msg.agent_id ?? null,
    content: formatWsContent(msg),
    symbol: msg.symbol || undefined,
    market: msg.market || undefined,
    side: msg.side || undefined,
    price: msg.price || undefined,
    quantity: msg.quantity || undefined,
    reactions: [],
  }));

  // Merge, deduplicate by id, sort newest first, cap
  const merged: FeedEvent[] = [...personalityFeed, ...legacyFeed, ...wsFeed];
  const deduped: FeedEvent[] = [];
  const seen = new Set<string>();
  for (const ev of merged) {
    if (seen.has(ev.id)) continue;
    seen.add(ev.id);
    deduped.push(ev);
  }
  deduped.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());

  return {
    events: deduped.slice(0, MAX_EVENTS),
    loading,
    error,
    lastUpdated,
    refresh: fetchPersonalityLog,
  };
}

function runnerLabelForEvent(e: PersonalityLogEvent): string {
  const runner = (e.runner || '').toLowerCase();
  const map: Record<string, string> = {
    scalprunner: 'ScalpRunner',
    blitzrunner: 'BlitzRunner',
    cryptorunner: 'CryptoRunner',
    orbrunner: 'ORBRunner',
  };
  return map[runner] || runner.charAt(0).toUpperCase() + runner.slice(1) || 'Unknown';
}

function formatWsContent(msg: WsActivityEvent): string {
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
