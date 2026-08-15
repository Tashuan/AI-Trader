import type { FeedEvent, FeedEventType, PersonalityPriority, RunnerKey } from '../types';
import { RUNNER_METADATA, runnerLabel, runnerColor } from '../types';

export interface TimelineFilters {
  runners: Set<string>;
  agents: Set<string>;
  eventTypes: Set<string>;
  timeRange: 'all' | '1h' | '4h' | 'today' | '24h';
}

export const DEFAULT_FILTERS: TimelineFilters = {
  runners: new Set(),
  agents: new Set(),
  eventTypes: new Set(),
  timeRange: 'all',
};

export const EVENT_TYPE_OPTIONS: { value: string; label: string; icon: string }[] = [
  { value: 'trade', label: 'Trades', icon: '⚡' },
  { value: 'entry', label: 'Entries', icon: '📈' },
  { value: 'exit', label: 'Exits', icon: '📉' },
  { value: 'order', label: 'Orders', icon: '📋' },
  { value: 'decision', label: 'Decisions', icon: '🎯' },
  { value: 'scan', label: 'Scans', icon: '🔍' },
  { value: 'phase', label: 'Phase', icon: '⏱️' },
  { value: 'thought', label: 'Thoughts', icon: '💭' },
  { value: 'strategy', label: 'Strategy', icon: '🧠' },
  { value: 'discussion', label: 'Discussion', icon: '💬' },
  { value: 'reply', label: 'Replies', icon: '↩️' },
  { value: 'error', label: 'Errors', icon: '⚠️' },
  { value: 'startup', label: 'Startup', icon: '🚀' },
  { value: 'shutdown', label: 'Shutdown', icon: '🛑' },
  { value: 'cycle_recap', label: 'Cycle Recap', icon: '📋' },
  { value: 'heartbeat', label: 'Heartbeat', icon: '💓' },
];

export const PRIORITY_OPTIONS: { value: PersonalityPriority; label: string; color: string }[] = [
  { value: 'critical', label: 'Critical', color: 'text-arena-red' },
  { value: 'error', label: 'Error', color: 'text-arena-red' },
  { value: 'trade', label: 'Trade', color: 'text-arena-green' },
  { value: 'action', label: 'Action', color: 'text-arena-orange' },
  { value: 'info', label: 'Info', color: 'text-arena-text-dim' },
];

export function applyFilters(events: FeedEvent[], filters: TimelineFilters): FeedEvent[] {
  const now = Date.now();
  const ranges: Record<string, number | null> = {
    '1h': 3600_000,
    '4h': 14400_000,
    '24h': 86400_000,
    today: null,
  };

  return events.filter(e => {
    if (filters.runners.size > 0 && !filters.runners.has((e.runner || '').toLowerCase())) return false;
    if (filters.agents.size > 0 && !filters.agents.has(e.agent)) return false;
    if (filters.eventTypes.size > 0 && !filters.eventTypes.has(e.type)) return false;

    if (filters.timeRange !== 'all') {
      const eventTime = new Date(e.timestamp).getTime();
      if (filters.timeRange === 'today') {
        const startOfDay = new Date();
        startOfDay.setHours(0, 0, 0, 0);
        if (eventTime < startOfDay.getTime()) return false;
      } else {
        const rangeMs = ranges[filters.timeRange];
        if (rangeMs && now - eventTime > rangeMs) return false;
      }
    }

    return true;
  });
}

export function extractRunners(events: FeedEvent[]): string[] {
  const runners = new Set<string>();
  for (const e of events) {
    if (e.runner) runners.add(e.runner.toLowerCase());
  }
  return Array.from(runners).sort();
}

export function extractAgents(events: FeedEvent[]): string[] {
  const agents = new Set<string>();
  for (const e of events) {
    if (e.agent && e.agent !== 'Unknown') agents.add(e.agent);
  }
  return Array.from(agents).sort();
}

export function extractEventTypes(events: FeedEvent[]): string[] {
  const types = new Set<string>();
  for (const e of events) {
    types.add(e.type);
  }
  return Array.from(types).sort();
}

export function groupByDate(events: FeedEvent[]): { label: string; events: FeedEvent[] }[] {
  const groups: { label: string; events: FeedEvent[] }[] = [];
  const labelMap = new Map<string, number>();

  for (const e of events) {
    const dt = new Date(e.timestamp);
    const dateKey = dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    const idx = labelMap.get(dateKey);
    if (idx === undefined) {
      labelMap.set(dateKey, groups.length);
      groups.push({ label: dateKey, events: [e] });
    } else {
      groups[idx].events.push(e);
    }
  }

  return groups;
}

export function getEventAccent(event: FeedEvent): { color: string; icon: string } {
  const type = event.type;
  const priority = event.priority;

  if (priority === 'error' || type === 'error') return { color: 'text-arena-red', icon: '⚠️' };
  if (priority === 'critical') return { color: 'text-arena-red', icon: '🔴' };
  if (type === 'trade' || type === 'entry' || type === 'exit') return { color: 'text-arena-green', icon: '⚡' };
  if (type === 'order') return { color: 'text-arena-green', icon: '📋' };
  if (type === 'decision') return { color: 'text-arena-purple', icon: '🎯' };
  if (type === 'scan') return { color: 'text-arena-blue', icon: '🔍' };
  if (type === 'strategy') return { color: 'text-arena-purple', icon: '🧠' };
  if (type === 'thought') return { color: 'text-arena-text-secondary', icon: '💭' };
  if (type === 'discussion') return { color: 'text-arena-blue', icon: '💬' };
  if (type === 'reply') return { color: 'text-arena-blue', icon: '↩️' };
  if (type === 'startup') return { color: 'text-arena-green', icon: '🚀' };
  if (type === 'shutdown') return { color: 'text-arena-red', icon: '🛑' };
  if (type === 'phase') return { color: 'text-arena-text-dim', icon: '⏱️' };
  if (type === 'heartbeat') return { color: 'text-arena-text-dim', icon: '💓' };
  if (type === 'cycle_recap') return { color: 'text-arena-orange', icon: '📋' };
  if (type === 'aggregate') return { color: 'text-arena-text-dim', icon: '📦' };

  return { color: 'text-arena-text-secondary', icon: '📊' };
}

export function formatTimestamp(ts: string): string {
  try {
    const dt = new Date(ts);
    return dt.toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: true,
      timeZone: 'America/New_York',
    });
  } catch {
    return '';
  }
}

export function formatRelativeTime(ts: string): string {
  try {
    const dt = new Date(ts).getTime();
    const diff = Date.now() - dt;
    if (diff < 60_000) return 'just now';
    if (diff < 3600_000) return `${Math.floor(diff / 60_000)}m ago`;
    if (diff < 86400_000) return `${Math.floor(diff / 3600_000)}h ago`;
    return `${Math.floor(diff / 86400_000)}d ago`;
  } catch {
    return '';
  }
}

export { RUNNER_METADATA, runnerLabel, runnerColor };
