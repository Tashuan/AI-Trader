import { useState, useMemo } from 'react';
import { Activity, AlertCircle, Loader2, RefreshCw } from 'lucide-react';
import type { TimelineEvent, WsActivityEvent } from '../types';
import { useTimelineData } from '../hooks/useTimelineData';
import {
  type TimelineFilters,
  DEFAULT_FILTERS,
  applyFilters,
  extractRunners,
  extractAgents,
  extractEventTypes,
  groupByDate,
  PRIORITY_OPTIONS,
} from '../utils/timelineFilters';
import { FilterToolbar } from '../components/FilterToolbar';
import { TimelineEventCard } from '../components/TimelineEventCard';

interface TimelinePageProps {
  timeline: TimelineEvent[];
  wsEvents?: WsActivityEvent[];
}

export function TimelinePage({ timeline, wsEvents = [] }: TimelinePageProps) {
  const { events, loading, error, lastUpdated, refresh } = useTimelineData(timeline, wsEvents);
  const [filters, setFilters] = useState<TimelineFilters>(DEFAULT_FILTERS);

  const availableRunners = useMemo(() => extractRunners(events), [events]);
  const availableAgents = useMemo(() => extractAgents(events), [events]);
  const availableEventTypes = useMemo(() => extractEventTypes(events), [events]);

  const filtered = useMemo(() => applyFilters(events, filters), [events, filters]);
  const grouped = useMemo(() => groupByDate(filtered), [filtered]);

  const activeFilterCount =
    filters.runners.size + filters.agents.size + filters.eventTypes.size +
    (filters.timeRange !== 'all' ? 1 : 0);

  return (
    <div className="flex-1 flex flex-col overflow-hidden p-4">
      {/* Header */}
      <div className="mb-3">
        <div className="flex items-center gap-2 mb-1">
          <Activity size={18} className="text-arena-green" />
          <h1 className="text-lg font-bold text-white">Event Timeline</h1>
          <span className="text-[10px] text-arena-text-dim ml-2">
            {filtered.length} event{filtered.length !== 1 ? 's' : ''}
          </span>
          {lastUpdated && (
            <span className="text-[10px] text-arena-text-dim ml-auto flex items-center gap-1">
              Updated {new Date(lastUpdated).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}
              <button
                onClick={refresh}
                className="ml-1 hover:text-white transition-colors"
                title="Refresh"
              >
                <RefreshCw size={10} />
              </button>
            </span>
          )}
        </div>
        <p className="text-xs text-arena-text-dim">
          Runner personality logs, trades, strategy posts, and operational events
        </p>
      </div>

      {/* Legend */}
      <div className="flex items-center gap-3 mb-3 text-[10px] text-arena-text-dim flex-wrap">
        <span className="flex items-center gap-1"><span className="text-arena-green">⚡</span> Trade</span>
        <span className="flex items-center gap-1"><span className="text-arena-purple">🎯</span> Decision</span>
        <span className="flex items-center gap-1"><span className="text-arena-blue">�</span> Scan</span>
        <span className="flex items-center gap-1"><span className="text-arena-orange">�</span> Recap</span>
        <span className="flex items-center gap-1"><span className="text-arena-red">⚠️</span> Error</span>
        <span className="flex items-center gap-1"><span className="text-arena-text-dim">💭</span> Thought</span>
        <span className="flex items-center gap-1"><span className="text-arena-blue">💬</span> Discussion</span>
        <span className="ml-auto flex items-center gap-2">
          {PRIORITY_OPTIONS.map(p => (
            <span key={p.value} className={`flex items-center gap-0.5 ${p.color}`}>
              <span className="w-1.5 h-1.5 rounded-full bg-current" />
              {p.label}
            </span>
          ))}
        </span>
      </div>

      {/* Filter toolbar */}
      <FilterToolbar
        filters={filters}
        onChange={setFilters}
        availableRunners={availableRunners}
        availableAgents={availableAgents}
        availableEventTypes={availableEventTypes}
      />

      {/* Main feed */}
      <div className="card-base p-3 flex-1 min-h-0 flex flex-col">
        {loading ? (
          <LoadingState />
        ) : error && events.length === 0 ? (
          <ErrorState error={error} onRetry={refresh} />
        ) : filtered.length === 0 ? (
          activeFilterCount > 0 ? (
            <EmptyAfterFilterState onReset={() => setFilters(DEFAULT_FILTERS)} />
          ) : (
            <NoEventsState />
          )
        ) : (
          <div className="space-y-0 overflow-y-auto flex-1">
            {error && events.length > 0 && (
              <div className="flex items-center gap-2 text-[10px] text-arena-orange bg-arena-orange/10 border border-arena-orange/20 rounded px-2 py-1 mb-2">
                <AlertCircle size={12} />
                Reconnecting… {error}
              </div>
            )}
            {grouped.map(group => (
              <div key={group.label}>
                <div className="sticky top-0 z-10 bg-arena-card/95 backdrop-blur-sm text-[10px] font-semibold text-arena-text-dim uppercase tracking-wider py-1.5 px-1 border-b border-arena-border">
                  {group.label}
                  <span className="ml-2 text-arena-text-dim/50 normal-case font-normal">
                    {group.events.length}
                  </span>
                </div>
                {group.events.map(event => (
                  <TimelineEventCard key={event.id} event={event} />
                ))}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="flex-1 flex items-center justify-center">
      <div className="text-center">
        <Loader2 size={20} className="text-arena-purple animate-spin mx-auto mb-2" />
        <div className="text-sm text-arena-text-dim">Loading timeline…</div>
      </div>
    </div>
  );
}

function ErrorState({ error, onRetry }: { error: string; onRetry: () => void }) {
  return (
    <div className="flex-1 flex items-center justify-center">
      <div className="text-center">
        <AlertCircle size={20} className="text-arena-red mx-auto mb-2" />
        <div className="text-sm text-arena-red mb-1">Failed to load timeline</div>
        <div className="text-xs text-arena-text-dim mb-3">{error}</div>
        <button
          onClick={onRetry}
          className="px-3 py-1.5 text-xs bg-arena-card border border-arena-border rounded-lg text-white hover:border-arena-border-hover transition-colors"
        >
          Retry
        </button>
      </div>
    </div>
  );
}

function EmptyAfterFilterState({ onReset }: { onReset: () => void }) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center">
      <div className="text-3xl mb-2 opacity-30">🔍</div>
      <div className="text-sm text-arena-text-dim mb-2">No events match your filters</div>
      <button
        onClick={onReset}
        className="text-xs text-arena-purple hover:text-white transition-colors"
      >
        Clear all filters
      </button>
    </div>
  );
}

function NoEventsState() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center">
      <div className="text-3xl mb-2 opacity-30">⏳</div>
      <div className="text-sm text-arena-text-dim italic">No events yet…</div>
      <div className="text-[10px] text-arena-text-dim mt-1">
        Events will appear here when runners start cycling
      </div>
    </div>
  );
}
