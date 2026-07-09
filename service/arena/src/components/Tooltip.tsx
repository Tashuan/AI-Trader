import { useState } from 'react';

interface TooltipProps {
  text: string;
  children: React.ReactNode;
  side?: 'top' | 'bottom' | 'left' | 'right';
}

export function Tooltip({ text, children, side = 'top' }: TooltipProps) {
  const [show, setShow] = useState(false);

  const positions: Record<string, string> = {
    top: 'bottom-full left-1/2 -translate-x-1/2 mb-1.5',
    bottom: 'top-full left-1/2 -translate-x-1/2 mt-1.5',
    left: 'right-full top-1/2 -translate-y-1/2 mr-1.5',
    right: 'left-full top-1/2 -translate-y-1/2 ml-1.5',
  };

  return (
    <div
      className="relative inline-flex"
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
    >
      {children}
      {show && (
        <div className={`absolute z-50 ${positions[side]} pointer-events-none whitespace-nowrap`}>
          <div className="bg-arena-card border border-arena-border rounded-md px-2 py-1 text-[10px] text-arena-text-secondary shadow-lg">
            {text}
          </div>
        </div>
      )}
    </div>
  );
}
