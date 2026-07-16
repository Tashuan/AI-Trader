import { motion, AnimatePresence } from 'framer-motion';
import { useState, useEffect } from 'react';
import type { Agent } from '../types';

interface AgentCardProps {
  agent: Agent;
  mentioned: boolean;
  onClick: () => void;
}

const ONLINE_COLOR = '#34D399';
const IDLE_COLOR = '#5A6275';

const ACTIVE_STATES = ['scanning', 'researching', 'reading_news', 'comparing_technicals', 'building_thesis', 'entering', 'managing', 'exiting', 'reviewing'];

const STATE_LABELS: Record<string, string> = {
  idle: 'IDLE',
  scanning: 'SCANNING',
  researching: 'RESEARCHING',
  reading_news: 'READING NEWS',
  comparing_technicals: 'COMPARING',
  building_thesis: 'BUILDING THESIS',
  waiting: 'WAITING',
  entering: 'ENTERING',
  managing: 'MANAGING',
  exiting: 'EXITING',
  reviewing: 'REVIEWING',
};

export function AgentCard({ agent, mentioned, onClick }: AgentCardProps) {
  const isActive = agent.online || agent.bot_running;
  const isWorking = isActive && ACTIVE_STATES.includes(agent.state);
  const stateColor = isWorking ? (agent.state_color || '#3B82F6') : (isActive ? ONLINE_COLOR : IDLE_COLOR);
  const stateLabel = isActive ? (STATE_LABELS[agent.state] || 'ONLINE') : 'IDLE';
  const position = agent.position;
  const [showFlash, setShowFlash] = useState(false);
  const [flashText, setFlashText] = useState('');

  // Flash last action when it changes
  useEffect(() => {
    if (!agent.last_action || !agent.last_action_at) return;
    const ageMs = Date.now() - agent.last_action_at;
    if (ageMs > 5000) return; // Only flash if recent
    setFlashText(agent.last_action);
    setShowFlash(true);
    const timer = setTimeout(() => setShowFlash(false), 3000);
    return () => clearTimeout(timer);
  }, [agent.last_action, agent.last_action_at]);

  return (
    <motion.div
      className={`card-base card-hover p-4 cursor-pointer relative overflow-hidden flex flex-col h-full ${mentioned ? 'glow-purple' : ''}`}
      style={{ borderColor: mentioned ? 'rgba(139,92,246,.4)' : undefined }}
      onClick={onClick}
      whileHover={{ y: -2 }}
    >
      {/* State border glow — pulses when actively working */}
      <div
        className="absolute top-0 left-0 right-0 h-0.5"
        style={{
          backgroundColor: stateColor,
          boxShadow: `0 0 8px ${stateColor}`,
          animation: isWorking ? `pulse-border 1.5s ease-in-out infinite` : undefined,
        }}
      />
      {isWorking && (
        <style>{`@keyframes pulse-border { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }`}</style>
      )}

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
            </div>
            <div className="text-[10px] text-arena-text-dim">{agent.goal}</div>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <span
            className="w-1.5 h-1.5 rounded-full"
            style={{
              backgroundColor: stateColor,
              animation: isWorking ? `pulse-dot 1s ease-in-out infinite` : undefined,
            }}
          />
          <span className="text-[9px] font-mono font-semibold" style={{ color: stateColor }}>
            {stateLabel}
          </span>
          {isWorking && (
            <span className="flex items-center gap-0.5 ml-0.5">
              <span className="w-0.5 h-0.5 rounded-full bg-current opacity-60" style={{ color: stateColor, animation: 'blink 1.4s infinite 0s' }} />
              <span className="w-0.5 h-0.5 rounded-full bg-current opacity-60" style={{ color: stateColor, animation: 'blink 1.4s infinite 0.2s' }} />
              <span className="w-0.5 h-0.5 rounded-full bg-current opacity-60" style={{ color: stateColor, animation: 'blink 1.4s infinite 0.4s' }} />
            </span>
          )}
          {isWorking && (
            <style>{`@keyframes pulse-dot { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.4); } } @keyframes blink { 0%, 80%, 100% { opacity: 0.2; } 40% { opacity: 1; } }`}</style>
          )}
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

      {/* Thought Stream (live conversational thoughts) */}
      <div className="mb-3">
        <div className="text-[9px] text-arena-text-dim mb-1">THOUGHTS</div>
        <div className="space-y-1 max-h-[120px] overflow-y-auto">
          {agent.thoughts && agent.thoughts.length > 0 ? (
            agent.thoughts.slice(0, 5).map((thought, i) => (
              <AnimatePresence mode="wait" key={`${thought}-${i}`}>
                <motion.div
                  initial={{ opacity: 0, y: -4 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.2 }}
                  className="text-[11px] text-arena-text-secondary leading-snug px-2 py-1 rounded bg-arena-bg/50 border-l-2"
                  style={{ borderColor: i === 0 && isActive ? '#3B82F6' : 'rgba(255,255,255,.06)' }}
                >
                  {thought}
                </motion.div>
              </AnimatePresence>
            ))
          ) : (
            <div className="text-[10px] italic text-arena-text-dim/50">
              {isActive ? 'Thinking...' : 'No recent thoughts'}
            </div>
          )}
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

      {/* Live Action Flash — shows recent trade/strategy/discussion */}
      <AnimatePresence>
        {showFlash && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.3 }}
            className="mb-2 overflow-hidden"
          >
            <div className="text-[10px] font-mono font-bold px-2 py-1 rounded bg-arena-bg border-l-2"
              style={{ borderColor: stateColor, color: stateColor }}
            >
              ⚡ {flashText}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Current Thesis */}
      <div className="mb-3 min-h-[40px]">
        <div className="text-[9px] text-arena-text-dim mb-0.5">CURRENT THESIS</div>
        <div className="text-[11px] text-arena-text-secondary line-clamp-2">
          {agent.thesis || <span className="text-arena-text-dim/50">No active thesis</span>}
        </div>
      </div>

      {/* Position Display */}
      <div className="mb-2 min-h-[20px] space-y-1">
        {agent.all_positions && agent.all_positions.length > 0 ? (
          agent.all_positions.map((pos, i) => (
            <div key={i} className="flex items-center justify-between">
              <div className="flex items-center gap-1.5">
                <span className={`text-[9px] font-mono font-bold ${pos.side === 'long' ? 'text-arena-green' : 'text-arena-red'}`}>
                  {pos.side.toUpperCase()}
                </span>
                <span className="text-[10px] font-mono text-white">{pos.symbol}</span>
              </div>
              <span className={`text-[10px] font-mono ${pos.pnl_pct >= 0 ? 'text-arena-green' : 'text-arena-red'}`}>
                {pos.pnl_pct >= 0 ? '+' : ''}{pos.pnl_pct.toFixed(1)}%
              </span>
            </div>
          ))
        ) : position ? (
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5">
              <span className={`text-[9px] font-mono font-bold ${position.side === 'long' ? 'text-arena-green' : 'text-arena-red'}`}>
                {position.side.toUpperCase()}
              </span>
              <span className="text-[10px] font-mono text-white">{position.symbol}</span>
            </div>
            <span className={`text-[10px] font-mono ${position.pnl_pct >= 0 ? 'text-arena-green' : 'text-arena-red'}`}>
              {position.pnl_pct >= 0 ? '+' : ''}{position.pnl_pct.toFixed(1)}%
            </span>
          </div>
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

      {/* Footer: Total P&L */}
      <div className="flex items-center justify-between pt-2 border-t border-arena-border mt-auto">
        <span className="text-[9px] text-arena-text-dim">
          TOTAL P&L
        </span>
        <div className="flex items-center gap-2">
          <span className={`text-[11px] font-mono font-semibold ${agent.total_profit >= 0 ? 'text-arena-green' : 'text-arena-red'}`}>
            {agent.total_profit >= 0 ? '+' : ''}${Math.abs(agent.total_profit).toLocaleString(undefined, { maximumFractionDigits: 0 })}
          </span>
          <span className={`text-[9px] font-mono ${agent.total_profit >= 0 ? 'text-arena-green' : 'text-arena-red'}`}>
            ({agent.total_profit >= 0 ? '+' : ''}{((agent.total_profit / 100000) * 100).toFixed(1)}%)
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
