import { motion, AnimatePresence } from 'framer-motion';
import { useState, useEffect, useRef } from 'react';
import type { Agent } from '../types';
import { AgentCard } from './AgentCard';

interface AgentArenaProps {
  agents: Agent[];
  mentionedAgent: string | null;
  onAgentClick: (agent: Agent) => void;
}

export function AgentArena({ agents, mentionedAgent, onAgentClick }: AgentArenaProps) {
  const sortedAgents = [...agents].sort((a, b) => {
    const aActive = a.online || a.bot_running;
    const bActive = b.online || b.bot_running;
    if (aActive !== bActive) return aActive ? -1 : 1;
    const aIdle = a.state === 'idle';
    const bIdle = b.state === 'idle';
    if (aIdle !== bIdle) return aIdle ? 1 : -1;
    return a.name.localeCompare(b.name);
  });

  return (
    <div className="flex-1 overflow-y-auto p-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4 gap-3">
        <AnimatePresence>
          {sortedAgents.map(agent => (
            <motion.div
              key={agent.agent_id}
              layout
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.9 }}
              transition={{ duration: 0.3 }}
            >
              <AgentCard
                agent={agent}
                mentioned={mentionedAgent === agent.name}
                onClick={() => onAgentClick(agent)}
              />
            </motion.div>
          ))}
        </AnimatePresence>
      </div>

      {agents.length === 0 && (
        <div className="flex items-center justify-center h-full">
          <div className="text-arena-text-dim text-sm">No agents in the arena yet...</div>
        </div>
      )}
    </div>
  );
}
