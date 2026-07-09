import { motion, AnimatePresence } from 'framer-motion';
import { useState, useEffect } from 'react';
import { Radio, MessageSquare, Newspaper, Activity } from 'lucide-react';
import type { CommentaryEntry, Headline, TimelineEvent } from '../types';

// ============================================================
// Commentary Panel — AI Announcer
// ============================================================

export function CommentaryPanel({ commentary }: { commentary: CommentaryEntry[] }) {
  return (
    <div className="card-base p-3 mb-3">
      <div className="flex items-center gap-2 mb-2">
        <Radio size={12} className="text-arena-purple" />
        <span className="text-[10px] font-semibold text-arena-purple tracking-wider">ARENA COMMENTATOR</span>
      </div>
      <div className="space-y-2 max-h-[200px] overflow-y-auto">
        <AnimatePresence>
          {commentary.map((entry, i) => (
            <motion.div
              key={`${entry.timestamp}-${i}`}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              className="text-[11px] text-arena-text-secondary leading-relaxed italic"
            >
              {entry.commentary}
            </motion.div>
          ))}
        </AnimatePresence>
        {commentary.length === 0 && (
          <div className="text-[10px] text-arena-text-dim italic">Waiting for commentary...</div>
        )}
      </div>
    </div>
  );
}

// ============================================================
// Conversation Panel — Agent Chat
// ============================================================

export function ConversationPanel({ timeline }: { timeline: TimelineEvent[] }) {
  const messages = timeline.filter(
    e => e.type === 'discussion' || e.type === 'strategy' || (e.type === 'trade' && e.reactions.length > 0)
  );

  return (
    <div className="card-base p-3 mb-3 flex-1 min-h-0 flex flex-col">
      <div className="flex items-center gap-2 mb-2 shrink-0">
        <MessageSquare size={12} className="text-arena-blue" />
        <span className="text-[10px] font-semibold text-arena-blue tracking-wider">AGENT CONVERSATION</span>
      </div>
      <div className="space-y-2 overflow-y-auto flex-1">
        <AnimatePresence>
          {messages.slice(0, 15).map((msg, i) => (
            <motion.div
              key={msg.id}
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              className="text-[11px]"
            >
              <span className="font-semibold text-white">{msg.agent}: </span>
              <span className="text-arena-text-secondary">{msg.content.replace(/^[^:]+:\s*/, '')}</span>
              {msg.reactions.map((reaction, j) => (
                <div key={j} className="ml-3 mt-0.5 text-[10px] text-arena-text-dim">
                  <span className="text-arena-purple">{reaction.agent}</span>
                  <span className="text-arena-text-dim"> {reaction.action}: </span>
                  <span>{reaction.detail}</span>
                </div>
              ))}
            </motion.div>
          ))}
        </AnimatePresence>
        {messages.length === 0 && (
          <div className="text-[10px] text-arena-text-dim italic">No conversations yet...</div>
        )}
      </div>
    </div>
  );
}

// ============================================================
// Headlines Panel — Arena Stories
// ============================================================

export function HeadlinesPanel({ headlines }: { headlines: Headline[] }) {
  const [currentIdx, setCurrentIdx] = useState(0);

  useEffect(() => {
    if (headlines.length <= 1) return;
    const interval = setInterval(() => {
      setCurrentIdx(prev => (prev + 1) % headlines.length);
    }, 5000);
    return () => clearInterval(interval);
  }, [headlines.length]);

  if (headlines.length === 0) return null;

  const headline = headlines[currentIdx];

  return (
    <div className="card-base p-3 mb-3">
      <div className="flex items-center gap-2 mb-2">
        <Newspaper size={12} className="text-arena-orange" />
        <span className="text-[10px] font-semibold text-arena-orange tracking-wider">ARENA HEADLINES</span>
      </div>
      <AnimatePresence mode="wait">
        <motion.div
          key={currentIdx}
          initial={{ opacity: 0, y: 5 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -5 }}
          className="text-[12px] text-white font-medium"
        >
          {headline.headline}
        </motion.div>
      </AnimatePresence>
      {headlines.length > 1 && (
        <div className="flex gap-1 mt-2">
          {headlines.map((_, i) => (
            <div
              key={i}
              className={`h-0.5 rounded-full transition-all ${i === currentIdx ? 'w-4 bg-arena-orange' : 'w-1 bg-arena-border'}`}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ============================================================
// Event Timeline Panel
// ============================================================

export function EventTimelinePanel({ timeline }: { timeline: TimelineEvent[] }) {
  return (
    <div className="card-base p-3 flex-1 min-h-0 flex flex-col">
      <div className="flex items-center gap-2 mb-2 shrink-0">
        <Activity size={12} className="text-arena-green" />
        <span className="text-[10px] font-semibold text-arena-green tracking-wider">EVENT TIMELINE</span>
      </div>
      <div className="space-y-1.5 overflow-y-auto flex-1">
        {timeline.slice(0, 20).map((event, i) => (
          <div key={event.id} className="flex items-start gap-2 text-[10px]">
            <span className="text-arena-text-dim font-mono shrink-0">
              {formatTime(event.timestamp)}
            </span>
            <span className={`shrink-0 ${event.type === 'trade' ? 'text-arena-green' : event.type === 'discussion' ? 'text-arena-blue' : 'text-arena-text-secondary'}`}>
              {event.type === 'trade' ? '⚡' : event.type === 'discussion' ? '💬' : '📊'}
            </span>
            <span className="text-arena-text-secondary line-clamp-2">{event.content}</span>
          </div>
        ))}
        {timeline.length === 0 && (
          <div className="text-[10px] text-arena-text-dim italic">No events yet...</div>
        )}
      </div>
    </div>
  );
}

function formatTime(timestamp: string): string {
  try {
    const dt = new Date(timestamp);
    return dt.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
  } catch {
    return '';
  }
}
