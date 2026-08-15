import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ChevronDown, ChevronRight } from 'lucide-react';
import type { FeedEvent } from '../types';
import { runnerLabel, runnerColor } from '../types';
import {
  getEventAccent,
  formatTimestamp,
  formatRelativeTime,
} from '../utils/timelineFilters';

interface TimelineEventCardProps {
  event: FeedEvent;
}

export function TimelineEventCard({ event }: TimelineEventCardProps) {
  const [expanded, setExpanded] = useState(false);
  const accent = getEventAccent(event);
  const hasDetail = Boolean(
    event.detail || event.facts || event.reactions.length > 0 || event.cycle_id,
  );

  return (
    <div
      className="border-b border-arena-border/50 last:border-0 py-2 px-1 hover:bg-white/[0.02] transition-colors"
    >
      {/* Compact row */}
      <div
        className="flex items-start gap-2.5 cursor-pointer"
        onClick={() => hasDetail && setExpanded(!expanded)}
        role={hasDetail ? 'button' : undefined}
        tabIndex={hasDetail ? 0 : undefined}
        onKeyDown={e => {
          if (hasDetail && (e.key === 'Enter' || e.key === ' ')) {
            e.preventDefault();
            setExpanded(!expanded);
          }
        }}
      >
        {/* Time */}
        <span className="text-arena-text-dim font-mono shrink-0 text-[10px] pt-0.5 min-w-[70px]">
          {formatTimestamp(event.timestamp)}
        </span>

        {/* Icon */}
        <span className={`shrink-0 text-xs pt-0.5 ${accent.color}`}>
          {accent.icon}
        </span>

        {/* Runner badge */}
        {event.runner && (
          <span
            className={`shrink-0 text-[9px] font-mono font-semibold bg-${runnerColor(event.runner)}/10 border border-${runnerColor(event.runner)}/20 rounded px-1.5 py-0.5 min-w-[85px] text-center text-${runnerColor(event.runner)}`}
          >
            {runnerLabel(event.runner)}
          </span>
        )}

        {/* Agent badge (when no runner, or agent differs) */}
        {(!event.runner || event.agent !== runnerLabel(event.runner || '')) && (
          <span className="shrink-0 text-[9px] font-mono font-semibold text-arena-green bg-arena-green/10 border border-arena-green/20 rounded px-1.5 py-0.5 min-w-[70px] text-center">
            {event.agent || '—'}
          </span>
        )}

        {/* Content */}
        <span className="text-arena-text-secondary text-xs flex-1 min-w-0">
          <span className="truncate">{event.content}</span>
          {event.symbol && (
            <span className="ml-1.5 text-[10px] font-mono text-arena-blue">
              {event.symbol}
            </span>
          )}
          {event.outcome && event.outcome !== 'observed' && (
            <span className="ml-1.5 text-[10px] text-arena-text-dim italic">
              ({event.outcome})
            </span>
          )}
        </span>

        {/* Relative time */}
        <span className="shrink-0 text-[9px] text-arena-text-dim pt-0.5">
          {formatRelativeTime(event.timestamp)}
        </span>

        {/* Expand indicator */}
        {hasDetail && (
          <span className="shrink-0 pt-0.5">
            {expanded ? (
              <ChevronDown size={12} className="text-arena-text-dim" />
            ) : (
              <ChevronRight size={12} className="text-arena-text-dim" />
            )}
          </span>
        )}
      </div>

      {/* Expanded detail */}
      <AnimatePresence>
        {expanded && hasDetail && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="overflow-hidden"
          >
            <div className="ml-[90px] mt-1.5 space-y-1.5 text-[10px] text-arena-text-dim">
              {event.detail && (
                <div>
                  <span className="text-arena-text-secondary font-semibold">Detail:</span>{' '}
                  {event.detail}
                </div>
              )}
              {event.cycle_id && (
                <div>
                  <span className="text-arena-text-secondary font-semibold">Cycle:</span>{' '}
                  <span className="font-mono">{event.cycle_id}</span>
                </div>
              )}
              {event.phase && (
                <div>
                  <span className="text-arena-text-secondary font-semibold">Phase:</span>{' '}
                  {event.phase}
                </div>
              )}
              {event.kind && (
                <div>
                  <span className="text-arena-text-secondary font-semibold">Kind:</span>{' '}
                  {event.kind}
                </div>
              )}
              {event.side && (
                <div>
                  <span className="text-arena-text-secondary font-semibold">Side:</span>{' '}
                  <span className={event.side === 'long' ? 'text-arena-green' : 'text-arena-red'}>
                    {event.side}
                  </span>
                </div>
              )}
              {event.price != null && event.price > 0 && (
                <div>
                  <span className="text-arena-text-secondary font-semibold">Price:</span>{' '}
                  <span className="font-mono">${event.price.toFixed(2)}</span>
                </div>
              )}
              {event.quantity != null && event.quantity > 0 && (
                <div>
                  <span className="text-arena-text-secondary font-semibold">Qty:</span>{' '}
                  <span className="font-mono">{event.quantity}</span>
                </div>
              )}
              {event.facts && Object.keys(event.facts).length > 0 && (
                <div>
                  <span className="text-arena-text-secondary font-semibold">Facts:</span>
                  <pre className="mt-0.5 ml-2 text-[9px] font-mono text-arena-text-dim overflow-x-auto">
                    {JSON.stringify(event.facts, null, 2)}
                  </pre>
                </div>
              )}
              {event.reactions.length > 0 && (
                <div>
                  <span className="text-arena-text-secondary font-semibold">Reactions:</span>
                  <div className="ml-2 mt-0.5 space-y-0.5">
                    {event.reactions.map((r, i) => (
                      <div key={i}>
                        <span className="text-arena-green">{r.agent}</span>{' '}
                        <span className="text-arena-text-dim">{r.action}:</span>{' '}
                        {r.detail}
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <div>
                <span className="text-arena-text-secondary font-semibold">Source:</span>{' '}
                <span className="font-mono">{event.source}</span>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
