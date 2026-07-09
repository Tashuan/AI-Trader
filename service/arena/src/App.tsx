import { useState } from 'react';
import { useArenaData } from './hooks/useArenaData';
import { TopMarketBar } from './components/TopMarketBar';
import { EventTicker } from './components/EventTicker';
import { AgentArena } from './components/AgentArena';
import { CommentaryPanel, ConversationPanel, HeadlinesPanel, EventTimelinePanel } from './components/SidePanels';
import { AgentDrawer } from './components/AgentDrawer';
import type { Agent } from './types';

export default function App() {
  const { data, loading, error, commentary, mentionedAgent } = useArenaData();
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);

  if (loading) {
    return (
      <div className="h-screen flex items-center justify-center bg-arena-bg">
        <div className="text-center">
          <div className="text-2xl font-bold text-white mb-2">AI TRADER <span className="text-arena-purple">ARENA</span></div>
          <div className="text-sm text-arena-text-dim animate-pulse">Initializing arena...</div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="h-screen flex items-center justify-center bg-arena-bg">
        <div className="text-center">
          <div className="text-sm text-arena-red mb-2">Failed to load arena</div>
          <div className="text-xs text-arena-text-dim">{error}</div>
          <button
            onClick={() => window.location.reload()}
            className="mt-4 px-4 py-2 text-xs bg-arena-card border border-arena-border rounded-lg text-white hover:border-arena-border-hover transition-colors"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  const markets = data?.markets || {};
  const agents = data?.agents || [];
  const headlines = data?.headlines || [];
  const timeline = data?.timeline || [];
  const breakingEvent = data?.breaking_event || null;

  return (
    <div className="h-screen flex flex-col bg-arena-bg overflow-hidden">
      {/* Top Market Bar */}
      <TopMarketBar markets={markets} breakingEvent={breakingEvent} />

      {/* Event Ticker */}
      <EventTicker events={timeline} />

      {/* Main Content Area */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left Sidebar: Commentary + Headlines + Conversation */}
        <div className="w-80 shrink-0 border-r border-arena-border p-3 flex flex-col overflow-hidden">
          <CommentaryPanel commentary={commentary} />
          <HeadlinesPanel headlines={headlines} />
          <ConversationPanel timeline={timeline} />
        </div>

        {/* Center: Agent Arena Grid */}
        <AgentArena
          agents={agents}
          mentionedAgent={mentionedAgent}
          onAgentClick={(agent) => setSelectedAgent(agent)}
        />

        {/* Right Sidebar: Event Timeline */}
        <div className="w-72 shrink-0 border-l border-arena-border p-3 flex flex-col overflow-hidden">
          <EventTimelinePanel timeline={timeline} />
        </div>
      </div>

      {/* Agent Drawer */}
      <AgentDrawer agent={selectedAgent} onClose={() => setSelectedAgent(null)} />
    </div>
  );
}
