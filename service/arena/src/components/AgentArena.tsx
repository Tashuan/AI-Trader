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
  return (
    <div className="flex-1 overflow-y-auto p-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4 gap-3">
        <AnimatePresence>
          {agents.map(agent => (
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
