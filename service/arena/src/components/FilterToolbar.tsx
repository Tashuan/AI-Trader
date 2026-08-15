import { useRef, useEffect, useState } from 'react';
import { Filter, Check, ChevronDown, X, RotateCcw } from 'lucide-react';
import type { TimelineFilters } from '../utils/timelineFilters';
import {
  EVENT_TYPE_OPTIONS,
  RUNNER_METADATA,
} from '../utils/timelineFilters';
import { runnerLabel } from '../types';

interface FilterToolbarProps {
  filters: TimelineFilters;
  onChange: (filters: TimelineFilters) => void;
  availableRunners: string[];
  availableAgents: string[];
  availableEventTypes: string[];
}

export function FilterToolbar({
  filters,
  onChange,
  availableRunners,
  availableAgents,
  availableEventTypes,
}: FilterToolbarProps) {
  const [openDropdown, setOpenDropdown] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpenDropdown(null);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const toggleSet = (set: Set<string>, value: string): Set<string> => {
    const next = new Set(set);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    return next;
  };

  const activeCount =
    filters.runners.size + filters.agents.size + filters.eventTypes.size +
    (filters.timeRange !== 'all' ? 1 : 0);

  const reset = () => {
    onChange({
      runners: new Set(),
      agents: new Set(),
      eventTypes: new Set(),
      timeRange: 'all',
    });
  };

  const allEventTypeOptions = EVENT_TYPE_OPTIONS.filter(o =>
    availableEventTypes.includes(o.value),
  );
  const otherEventTypes = availableEventTypes.filter(
    t => !EVENT_TYPE_OPTIONS.some(o => o.value === t),
  );

  return (
    <div ref={containerRef} className="flex items-center gap-2 mb-3 flex-wrap">
      <div className="flex items-center gap-1.5">
        <Filter size={12} className="text-arena-text-dim" />
      </div>

      {/* Runner filter */}
      <DropdownButton
        label="Runners"
        count={filters.runners.size}
        isOpen={openDropdown === 'runners'}
        onToggle={() => setOpenDropdown(openDropdown === 'runners' ? null : 'runners')}
      >
        {availableRunners.map(r => {
          const checked = filters.runners.has(r);
          const meta = RUNNER_METADATA.find(m => m.key === r);
          return (
            <CheckboxRow
              key={r}
              checked={checked}
              onClick={() => onChange({ ...filters, runners: toggleSet(filters.runners, r) })}
            >
              <span className={`text-${meta?.color || 'arena-purple'}`}>●</span>
              <span className="text-[11px] text-white/80">{runnerLabel(r)}</span>
            </CheckboxRow>
          );
        })}
        {availableRunners.length === 0 && (
          <div className="text-[10px] text-arena-text-dim px-2 py-1.5">No runners in feed</div>
        )}
        {filters.runners.size > 0 && <ClearButton onClick={() => onChange({ ...filters, runners: new Set() })} />}
      </DropdownButton>

      {/* Agent filter */}
      <DropdownButton
        label="Agents"
        count={filters.agents.size}
        isOpen={openDropdown === 'agents'}
        onToggle={() => setOpenDropdown(openDropdown === 'agents' ? null : 'agents')}
      >
        {availableAgents.map(a => {
          const checked = filters.agents.has(a);
          return (
            <CheckboxRow
              key={a}
              checked={checked}
              onClick={() => onChange({ ...filters, agents: toggleSet(filters.agents, a) })}
            >
              <span className="text-[11px] text-white/80">{a}</span>
            </CheckboxRow>
          );
        })}
        {availableAgents.length === 0 && (
          <div className="text-[10px] text-arena-text-dim px-2 py-1.5">No agents in feed</div>
        )}
        {filters.agents.size > 0 && <ClearButton onClick={() => onChange({ ...filters, agents: new Set() })} />}
      </DropdownButton>

      {/* Event type filter */}
      <DropdownButton
        label="Event Types"
        count={filters.eventTypes.size}
        isOpen={openDropdown === 'types'}
        onToggle={() => setOpenDropdown(openDropdown === 'types' ? null : 'types')}
      >
        {allEventTypeOptions.map(o => {
          const checked = filters.eventTypes.has(o.value);
          return (
            <CheckboxRow
              key={o.value}
              checked={checked}
              onClick={() => onChange({ ...filters, eventTypes: toggleSet(filters.eventTypes, o.value) })}
            >
              <span className="text-xs">{o.icon}</span>
              <span className="text-[11px] text-white/80">{o.label}</span>
            </CheckboxRow>
          );
        })}
        {otherEventTypes.map(t => {
          const checked = filters.eventTypes.has(t);
          return (
            <CheckboxRow
              key={t}
              checked={checked}
              onClick={() => onChange({ ...filters, eventTypes: toggleSet(filters.eventTypes, t) })}
            >
              <span className="text-[11px] text-white/80">{t}</span>
            </CheckboxRow>
          );
        })}
        {availableEventTypes.length === 0 && (
          <div className="text-[10px] text-arena-text-dim px-2 py-1.5">No events in feed</div>
        )}
        {filters.eventTypes.size > 0 && <ClearButton onClick={() => onChange({ ...filters, eventTypes: new Set() })} />}
      </DropdownButton>

      {/* Time range filter */}
      <select
        value={filters.timeRange}
        onChange={e => onChange({ ...filters, timeRange: e.target.value as TimelineFilters['timeRange'] })}
        className="form-input w-auto min-w-[100px]"
      >
        <option value="all">All Time</option>
        <option value="1h">Last 1h</option>
        <option value="4h">Last 4h</option>
        <option value="today">Today</option>
        <option value="24h">Last 24h</option>
      </select>

      {/* Active filter chips */}
      {activeCount > 0 && (
        <div className="flex items-center gap-1 ml-1">
          {[...filters.runners].map(r => (
            <FilterChip key={`r-${r}`} label={runnerLabel(r)} onRemove={() => onChange({ ...filters, runners: toggleSet(filters.runners, r) })} />
          ))}
          {[...filters.agents].map(a => (
            <FilterChip key={`a-${a}`} label={a} onRemove={() => onChange({ ...filters, agents: toggleSet(filters.agents, a) })} />
          ))}
          {[...filters.eventTypes].map(t => (
            <FilterChip key={`t-${t}`} label={t} onRemove={() => onChange({ ...filters, eventTypes: toggleSet(filters.eventTypes, t) })} />
          ))}
          {filters.timeRange !== 'all' && (
            <FilterChip label={filters.timeRange} onRemove={() => onChange({ ...filters, timeRange: 'all' })} />
          )}
          <button
            onClick={reset}
            className="flex items-center gap-1 text-[10px] text-arena-text-dim hover:text-arena-red px-1.5 py-0.5"
            title="Clear all filters"
          >
            <RotateCcw size={10} />
            Reset
          </button>
        </div>
      )}
    </div>
  );
}

function DropdownButton({
  label,
  count,
  isOpen,
  onToggle,
  children,
}: {
  label: string;
  count: number;
  isOpen: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="relative">
      <button
        onClick={onToggle}
        className="form-input w-auto min-w-[110px] flex items-center justify-between gap-1.5 cursor-pointer"
      >
        <span className="truncate">
          {count === 0 ? label : count === 1 ? `${count} selected` : `${count} selected`}
        </span>
        <ChevronDown size={12} className={`text-arena-text-dim shrink-0 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>
      {isOpen && (
        <div className="absolute top-full left-0 mt-1 z-30 min-w-[160px] max-h-[240px] overflow-y-auto card-base p-1 shadow-xl">
          {children}
        </div>
      )}
    </div>
  );
}

function CheckboxRow({
  checked,
  onClick,
  children,
}: {
  checked: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className="w-full flex items-center gap-2 px-2 py-1.5 rounded hover:bg-white/5 text-left"
    >
      <span className={`w-3.5 h-3.5 rounded border flex items-center justify-center shrink-0 ${checked ? 'bg-arena-purple border-arena-purple' : 'border-white/20'}`}>
        {checked && <Check size={10} className="text-white" />}
      </span>
      {children}
    </button>
  );
}

function ClearButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="w-full text-[10px] text-arena-text-dim hover:text-white px-2 py-1.5 border-t border-arena-border mt-1"
    >
      Clear
    </button>
  );
}

function FilterChip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 text-[9px] bg-arena-purple/15 border border-arena-purple/30 text-arena-purple rounded px-1.5 py-0.5">
      {label}
      <button onClick={onRemove} className="hover:text-white">
        <X size={9} />
      </button>
    </span>
  );
}
