import { useState, useEffect } from 'react';
import { Activity } from 'lucide-react';

type BgTaskStatus = {
  enabled_by_default: boolean;
  enabled_tasks: string[];
  running: { name: string; done: boolean; cancelled: boolean }[];
  active_count: number;
  total_count: number;
};

export function BackgroundTaskIndicator() {
  const [status, setStatus] = useState<BgTaskStatus | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    const poll = async () => {
      try {
        const res = await fetch('/api/background-tasks/status');
        if (res.ok) {
          setStatus(await res.json());
        }
      } catch {
        // silent fail
      }
    };
    poll();
    const interval = setInterval(poll, 10000);
    return () => clearInterval(interval);
  }, []);

  if (!status) return null;

  const isActive = (status.active_count || 0) > 0;
  const dotColor = isActive ? '#34D399' : '#5A6275';
  const label = isActive
    ? `${status.active_count}/${status.total_count} tasks`
    : 'No tasks';

  return (
    <div className="relative" onClick={(e) => e.stopPropagation()}>
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-1.5 px-2 py-1.5 rounded-lg hover:bg-white/5 transition-colors"
        title="Background tasks"
      >
        <Activity size={12} className="text-arena-text-dim shrink-0" />
        <span
          className="w-1.5 h-1.5 rounded-full shrink-0"
          style={{
            backgroundColor: dotColor,
            boxShadow: isActive ? `0 0 4px ${dotColor}` : undefined,
          }}
        />
        <span className="text-[9px] font-mono text-arena-text-dim truncate">{label}</span>
      </button>

      {expanded && (
        <div className="absolute bottom-full left-0 mb-1 w-56 bg-arena-card border border-arena-border rounded-lg p-3 shadow-xl z-50">
          <div className="text-[10px] font-semibold text-white mb-2">Background Tasks</div>
          <div className="text-[9px] text-arena-text-dim mb-2">
            Default enabled: {status.enabled_by_default ? 'Yes' : 'No'}
          </div>
          {status.running.length > 0 ? (
            <div className="space-y-1 max-h-48 overflow-y-auto">
              {status.running.map((task) => (
                <div key={task.name} className="flex items-center justify-between text-[9px]">
                  <span className="text-arena-text-secondary truncate">{task.name}</span>
                  <span
                    className="font-mono shrink-0 ml-2"
                    style={{
                      color: task.cancelled
                        ? '#EF4444'
                        : task.done
                        ? '#5A6275'
                        : '#34D399',
                    }}
                  >
                    {task.cancelled ? 'cancelled' : task.done ? 'done' : 'running'}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-[9px] text-arena-text-dim">No tasks registered</div>
          )}
        </div>
      )}
    </div>
  );
}
