import { motion, AnimatePresence } from 'framer-motion';
import { useState } from 'react';
import { LayoutDashboard, ChevronLeft, ChevronRight, Settings, TrendingUp, Users, Activity, Target } from 'lucide-react';
import { BackgroundTaskIndicator } from './BackgroundTaskIndicator';

export type PageId = 'arena' | 'agents' | 'markets' | 'positions' | 'timeline' | 'settings';

interface NavItem {
  id: PageId;
  label: string;
  icon: React.ReactNode;
  description: string;
}

const NAV_ITEMS: NavItem[] = [
  { id: 'arena', label: 'Arena', icon: <LayoutDashboard size={16} />, description: 'Live agent dashboard' },
  { id: 'agents', label: 'Agents', icon: <Users size={16} />, description: 'Launch & manage agents' },
  { id: 'markets', label: 'Markets', icon: <TrendingUp size={16} />, description: 'Market battlefield' },
  { id: 'positions', label: 'Positions', icon: <Target size={16} />, description: 'SL/TP position tracker' },
  { id: 'timeline', label: 'Timeline', icon: <Activity size={16} />, description: 'Event & trade history' },
  { id: 'settings', label: 'Settings', icon: <Settings size={16} />, description: 'Configuration' },
];

interface SidebarNavProps {
  currentPage: PageId;
  onNavigate: (page: PageId) => void;
}

export function SidebarNav({ currentPage, onNavigate }: SidebarNavProps) {
  const [collapsed, setCollapsed] = useState(true);

  return (
    <div
      className={`shrink-0 border-r border-arena-border bg-arena-card/80 flex flex-col transition-all duration-200 ${
        collapsed ? 'w-14' : 'w-48'
      }`}
    >
      {/* Logo / Collapse button */}
      <div className="h-[90px] border-b border-arena-border flex items-center justify-center shrink-0">
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="flex items-center gap-2 text-arena-text-secondary hover:text-white transition-colors"
        >
          {!collapsed && (
            <span className="text-xs font-bold tracking-tight">
              AI<span className="text-arena-purple">.</span>
            </span>
          )}
          {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>
      </div>

      {/* Nav items */}
      <nav className="flex-1 py-3 space-y-1 px-2">
        {NAV_ITEMS.map(item => {
          const active = currentPage === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onNavigate(item.id)}
              className={`w-full flex items-center gap-2.5 px-2 py-2 rounded-lg transition-all group relative ${
                active
                  ? 'bg-arena-purple/15 text-arena-purple'
                  : 'text-arena-text-secondary hover:bg-white/5 hover:text-white'
              }`}
              title={collapsed ? item.label : undefined}
            >
              <span className="shrink-0">{item.icon}</span>
              {!collapsed && (
                <div className="flex flex-col items-start overflow-hidden">
                  <span className="text-[11px] font-semibold">{item.label}</span>
                  <span className="text-[9px] text-arena-text-dim truncate">{item.description}</span>
                </div>
              )}
              {active && !collapsed && (
                <motion.div
                  layoutId="nav-active"
                  className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-6 bg-arena-purple rounded-r"
                />
              )}
            </button>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="p-2 border-t border-arena-border shrink-0">
        <BackgroundTaskIndicator />
        {!collapsed && (
          <div className="px-2 pt-1">
            <div className="text-[9px] text-arena-text-dim">Stockboy</div>
            <div className="text-[9px] text-arena-text-dim">v1.0</div>
          </div>
        )}
      </div>
    </div>
  );
}
