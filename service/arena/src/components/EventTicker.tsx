import { motion, AnimatePresence } from 'framer-motion';
import type { TimelineEvent } from '../types';

export function EventTicker({ events }: { events: TimelineEvent[] }) {
  if (events.length === 0) return null;

  const items = events.slice(0, 15);
  const doubled = [...items, ...items];

  return (
    <div className="h-7 border-b border-arena-border bg-arena-card/30 overflow-hidden flex items-center">
      <div className="shrink-0 px-3 h-full flex items-center bg-arena-purple/10 border-r border-arena-border">
        <span className="text-[9px] font-mono font-bold text-arena-purple tracking-wider">FEED</span>
      </div>
      <div className="flex-1 overflow-hidden">
        <div className="flex items-center gap-6 whitespace-nowrap">
          {doubled.map((event, i) => (
            <span key={`${event.id}-${i}`} className="text-[10px] text-arena-text-secondary inline-flex items-center gap-1.5">
              <span className={`text-[8px] ${
                event.type === 'trade' ? 'text-arena-green' :
                event.type === 'discussion' ? 'text-arena-blue' :
                'text-arena-text-dim'
              }`}>
                {event.type === 'trade' ? '⚡' : event.type === 'discussion' ? '💬' : '📊'}
              </span>
              <span className="font-mono text-arena-text-dim">{event.agent}</span>
              <span>{event.content.replace(/^[^:]+:\s*/, '').slice(0, 80)}</span>
              <span className="text-arena-border">|</span>
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
