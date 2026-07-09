import { useState } from 'react';
import { useArenaData } from './hooks/useArenaData';
import { SidebarNav, type PageId } from './components/SidebarNav';
import { TopMarketBar } from './components/TopMarketBar';
import { EventTicker } from './components/EventTicker';
import { AgentArena } from './components/AgentArena';
import { CommentaryPanel, ConversationPanel, HeadlinesPanel, EventTimelinePanel } from './components/SidePanels';
import { AgentDrawer } from './components/AgentDrawer';
import { MarketsPage } from './pages/MarketsPage';
import { SettingsPage } from './pages/SettingsPage';
import { AgentsPage } from './pages/AgentsPage';
import type { Agent } from './types';

export default function App() {
  const { data, loading, error, commentary, mentionedAgent } = useArenaData();
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);
  const [currentPage, setCurrentPage] = useState<PageId>('arena');

  if (loading) {
    return (
      <div className="h-screen flex items-center justify-center bg-arena-bg">
        <div className="text-center">
          <div className="text-2xl font-bold text-white mb-2">STOCK<span className="text-arena-purple">BOY</span></div>
          <div className="text-sm text-arena-text-dim animate-pulse">Initializing stockboy...</div>
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
    <div className="h-screen flex bg-arena-bg overflow-hidden">
      {/* Collapsible Sidebar Nav */}
      <SidebarNav currentPage={currentPage} onNavigate={setCurrentPage} />

      {/* Main column */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Top Market Bar */}
        <TopMarketBar markets={markets} breakingEvent={breakingEvent} />

        {/* Event Ticker */}
        <EventTicker events={timeline} />

        {/* Page Content */}
        {currentPage === 'arena' && (
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
        )}

        {currentPage === 'markets' && (
          <MarketsPage markets={markets} />
        )}

        {currentPage === 'agents' && (
          <AgentsPage />
        )}

        {currentPage === 'settings' && (
          <SettingsPage />
        )}
      </div>

      {/* Agent Drawer (available from any page) */}
      <AgentDrawer agent={selectedAgent} onClose={() => setSelectedAgent(null)} />
    </div>
  );
}
