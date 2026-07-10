import { Activity } from 'lucide-react';
import type { TimelineEvent } from '../types';

interface TimelinePageProps {
  timeline: TimelineEvent[];
}

export function TimelinePage({ timeline }: TimelinePageProps) {
  const events = timeline.filter(e => e.type !== 'discussion').slice(0, 100);

  return (
    <div className="flex-1 flex flex-col overflow-hidden p-6">
      <div className="flex items-center gap-2 mb-1">
        <Activity size={18} className="text-arena-green" />
        <h1 className="text-lg font-bold text-white">Event Timeline</h1>
      </div>
      <p className="text-xs text-arena-text-dim mb-3">Trades, strategy posts, replies, and operational events</p>

      <div className="flex items-center gap-4 mb-4 text-[10px] text-arena-text-dim">
        <span className="flex items-center gap-1"><span className="text-arena-green">⚡</span> Trade</span>
        <span className="flex items-center gap-1"><span className="text-arena-purple">🎯</span> Strategy</span>
        <span className="flex items-center gap-1"><span className="text-arena-blue">💬</span> Reply</span>
        <span className="flex items-center gap-1"><span className="text-arena-text-secondary">📊</span> Other</span>
      </div>

      <div className="card-base p-4 flex-1 min-h-0 flex flex-col">
        <div className="space-y-2 overflow-y-auto flex-1">
          {events.map((event) => (
            <div key={event.id} className="flex items-start gap-3 text-sm py-1.5 border-b border-arena-border/50 last:border-0">
              <span className="text-arena-text-dim font-mono shrink-0 text-xs">
                {formatTime(event.timestamp)}
              </span>
              <span className={`shrink-0 ${event.type === 'trade' ? 'text-arena-green' : event.type === 'strategy' ? 'text-arena-purple' : event.type === 'reply' ? 'text-arena-blue' : 'text-arena-text-secondary'}`}>
                {event.type === 'trade' ? '⚡' : event.type === 'strategy' ? '🎯' : event.type === 'reply' ? '💬' : '📊'}
              </span>
              <span className="text-arena-text-secondary">{event.content}</span>
            </div>
          ))}
          {events.length === 0 && (
            <div className="flex items-center justify-center h-full">
              <div className="text-sm text-arena-text-dim italic">No events yet...</div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function formatTime(timestamp: string): string {
  try {
    const dt = new Date(timestamp);
    return dt.toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      hour12: true,
      timeZone: 'America/New_York',
    });
  } catch {
    return '';
  }
}
