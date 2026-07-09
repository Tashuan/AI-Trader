import { motion, AnimatePresence } from 'framer-motion';
import { useState, useEffect, useRef } from 'react';
import type { Agent } from '../types';

interface AgentCardProps {
  agent: Agent;
  mentioned: boolean;
  onClick: () => void;
}

const ONLINE_COLOR = '#34D399';
const IDLE_COLOR = '#5A6275';

export function AgentCard({ agent, mentioned, onClick }: AgentCardProps) {
  const isActive = agent.online || agent.bot_running;
  const stateColor = isActive ? ONLINE_COLOR : IDLE_COLOR;
  const stateLabel = isActive ? 'ONLINE' : 'IDLE';
  const pnlPositive = agent.today_pnl >= 0;
  const position = agent.position;
  const [thinkingText, setThinkingText] = useState(agent.state_detail || '');

  // Rotate thinking feed text from quirks
  useEffect(() => {
    if (agent.state_detail) {
      setThinkingText(agent.state_detail);
      return;
    }
    const quirks = agent.quirks || [];
    if (quirks.length === 0) return;

    let idx = 0;
    setThinkingText(quirks[0]);
    const interval = setInterval(() => {
      idx = (idx + 1) % quirks.length;
      setThinkingText(quirks[idx]);
    }, 4000);

    return () => clearInterval(interval);
  }, [agent.state_detail, agent.quirks]);

  return (
    <motion.div
      className={`card-base card-hover p-4 cursor-pointer relative overflow-hidden flex flex-col h-full ${mentioned ? 'glow-purple' : ''}`}
      style={{ borderColor: mentioned ? 'rgba(139,92,246,.4)' : undefined }}
      onClick={onClick}
      whileHover={{ y: -2 }}
      layout
    >
      {/* State border glow */}
      <div
        className="absolute top-0 left-0 right-0 h-0.5"
        style={{ backgroundColor: stateColor, boxShadow: `0 0 8px ${stateColor}` }}
      />

      {/* Header: Name + Status */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className="relative">
            <Avatar name={agent.name} />
            {isActive && (
              <div
                className="absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-arena-card bg-arena-green"
                style={{ boxShadow: '0 0 4px #34D399' }}
              />
            )}
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <span className="text-sm font-semibold text-white">{agent.name}</span>
              {isActive && (
                <span className="text-[8px] font-mono font-semibold text-arena-green uppercase">Online</span>
              )}
            </div>
            <div className="text-[10px] text-arena-text-dim">{agent.goal}</div>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <motion.span
            className="w-1.5 h-1.5 rounded-full"
            style={{ backgroundColor: stateColor }}
            animate={isActive ? { opacity: [1, 0.4, 1] } : { opacity: 1 }}
            transition={isActive ? { duration: 2, repeat: Infinity } : { duration: 0 }}
          />
          <span className="text-[9px] font-mono font-semibold" style={{ color: stateColor }}>
            {stateLabel}
          </span>
        </div>
      </div>

      {/* Watching symbol */}
      <div className="mb-2 min-h-[16px]">
        {agent.state_symbol ? (
          <>
            <span className="text-[10px] text-arena-text-dim">Watching: </span>
            <span className="text-[10px] font-mono text-arena-blue">{agent.state_symbol}</span>
          </>
        ) : (
          <span className="text-[10px] text-arena-text-dim/50">—</span>
        )}
      </div>

      {/* Current Thesis */}
      <div className="mb-3 min-h-[40px]">
        <div className="text-[9px] text-arena-text-dim mb-0.5">CURRENT THESIS</div>
        <div className="text-[11px] text-arena-text-secondary line-clamp-2">
          {agent.thesis || <span className="text-arena-text-dim/50">No active thesis</span>}
        </div>
      </div>

      {/* Confidence Meter */}
      <div className="mb-3">
        <div className="flex items-center justify-between mb-1">
          <span className="text-[9px] text-arena-text-dim">CONFIDENCE</span>
          <span className="text-[9px] font-mono" style={{ color: stateColor }}>
            {agent.confidence_label}
          </span>
        </div>
        <div className="h-1.5 bg-arena-bg rounded-full overflow-hidden">
          <motion.div
            className="h-full rounded-full"
            style={{ backgroundColor: stateColor }}
            initial={{ width: 0 }}
            animate={{ width: `${Math.max(agent.confidence * 100, 2)}%` }}
            transition={{ duration: 0.5 }}
          />
        </div>
      </div>

      {/* Thinking Feed (rotating) */}
      <div className="mb-3 min-h-[28px]">
        <AnimatePresence mode="wait">
          <motion.div
            key={thinkingText}
            initial={{ opacity: 0, y: 5 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -5 }}
            transition={{ duration: 0.3 }}
            className="text-[10px] italic text-arena-text-secondary"
          >
            {thinkingText}
          </motion.div>
        </AnimatePresence>
      </div>

      {/* Position Display */}
      <div className="mb-2 min-h-[20px] flex items-center justify-between">
        {position ? (
          <>
            <div className="flex items-center gap-1.5">
              <span className={`text-[9px] font-mono font-bold ${position.side === 'long' ? 'text-arena-green' : 'text-arena-red'}`}>
                {position.side.toUpperCase()}
              </span>
              <span className="text-[10px] font-mono text-white">{position.symbol}</span>
            </div>
            <span className={`text-[10px] font-mono ${position.pnl_pct >= 0 ? 'text-arena-green' : 'text-arena-red'}`}>
              {position.pnl_pct >= 0 ? '+' : ''}{position.pnl_pct.toFixed(1)}%
            </span>
          </>
        ) : (
          <span className="text-[10px] text-arena-text-dim/50">No open position</span>
        )}
      </div>

      {/* Relationship + Memory */}
      <div className="space-y-1 mb-2 min-h-[28px] flex-1">
        {agent.relationship_focus ? (
          <div className="text-[9px] text-arena-purple truncate">
            {agent.relationship_focus}
          </div>
        ) : (
          <div className="text-[9px] text-arena-text-dim/50 truncate">&nbsp;</div>
        )}
        {agent.memories.length > 0 ? (
          <div className="text-[9px] text-arena-text-dim truncate">
            {agent.memories[0]}
          </div>
        ) : (
          <div className="text-[9px] text-arena-text-dim/50 truncate">&nbsp;</div>
        )}
      </div>

      {/* Footer: Today's P&L */}
      <div className="flex items-center justify-between pt-2 border-t border-arena-border mt-auto">
        <span className="text-[9px] text-arena-text-dim">TODAY</span>
        <div className="flex items-center gap-2">
          <span className={`text-[11px] font-mono font-semibold ${pnlPositive ? 'text-arena-green' : 'text-arena-red'}`}>
            {pnlPositive ? '+' : ''}${Math.abs(agent.today_pnl).toLocaleString(undefined, { maximumFractionDigits: 0 })}
          </span>
          <span className={`text-[9px] font-mono ${pnlPositive ? 'text-arena-green' : 'text-arena-red'}`}>
            ({pnlPositive ? '+' : ''}{agent.today_pnl_pct.toFixed(1)}%)
          </span>
        </div>
      </div>
    </motion.div>
  );
}

function Avatar({ name }: { name: string }) {
  const seed = name.toLowerCase().replace(/[^a-z0-9]/g, '');
  const hue = seed.charCodeAt(0) * 37 % 360;
  const initials = name.split(/(?=[A-Z])/).map(w => w[0]).join('').slice(0, 2).toUpperCase();

  return (
    <div
      className="w-8 h-8 rounded-lg flex items-center justify-center text-[10px] font-bold text-white shrink-0"
      style={{
        background: `linear-gradient(135deg, hsl(${hue}, 60%, 30%), hsl(${(hue + 40) % 360}, 60%, 20%))`,
        border: '1px solid rgba(255,255,255,.08)',
      }}
    >
      {initials}
    </div>
  );
}
