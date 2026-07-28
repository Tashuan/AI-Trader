import { useState, useEffect } from 'react';
import { Volume2, VolumeX } from 'lucide-react';
import { isMuted, setMuted } from '../utils/sounds';

// DISABLED: Market chips and breaking event banner — re-enable by restoring props and markup
// import { motion } from 'framer-motion';
// import type { MarketData } from '../types';

interface TopMarketBarProps {
  // DISABLED: markets and breakingEvent no longer needed for the header bar
  // markets?: Record<string, MarketData>;
  // breakingEvent?: { headline: string; source: string; timestamp: string } | null;
}

export function TopMarketBar({}: TopMarketBarProps = {}) {
  return (
    <div className="relative h-[48px] border-b border-arena-border bg-arena-card/80 flex items-center px-4 gap-3 overflow-hidden">
      {/* Logo + LIVE indicator */}
      <div className="flex items-center gap-3 shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold tracking-tight text-white">STOCK</span>
          <span className="text-sm font-bold text-arena-purple">BOY</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span
            className="w-2 h-2 rounded-full bg-arena-red"
            style={{ boxShadow: '0 0 6px rgba(239,68,68,0.5)' }}
          />
          <span className="text-[10px] font-mono text-arena-red">LIVE</span>
        </div>
      </div>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Sound toggle */}
      <SoundToggle />

      {/* Clock */}
      <div className="shrink-0 text-right">
        <Clock />
      </div>
    </div>
  );
}

function Clock() {
  const [time, setTime] = useState(new Date());

  useEffect(() => {
    const interval = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="flex flex-col items-end">
      <span className="text-xs font-mono text-arena-text-secondary">
        {time.toLocaleTimeString('en-US', { hour12: false, timeZone: 'America/New_York' })}
      </span>
      <span className="text-[9px] text-arena-text-dim">EST</span>
    </div>
  );
}

function SoundToggle() {
  const [muted, setMutedState] = useState(isMuted());

  const toggle = () => {
    const next = !muted;
    setMuted(next);
    setMutedState(next);
  };

  return (
    <button
      onClick={toggle}
      className="shrink-0 p-1.5 rounded-lg text-arena-text-dim hover:text-arena-text-secondary hover:bg-arena-bg/60 transition-colors"
      title={muted ? 'Unmute sounds' : 'Mute sounds'}
    >
      {muted ? <VolumeX size={16} /> : <Volume2 size={16} />}
    </button>
  );
}

