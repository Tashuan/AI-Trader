import type { MarketData } from '../types';

interface MarketsPageProps {
  markets: Record<string, MarketData>;
}

export function MarketsPage({ markets }: MarketsPageProps) {
  const symbols = Object.keys(markets);

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <h1 className="text-lg font-bold text-white mb-1">Market Battlefield</h1>
      <p className="text-xs text-arena-text-dim mb-6">Agent attention and positioning across markets</p>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {symbols.map(sym => {
          const data = markets[sym];
          const watching = data.agents_watching || 0;
          const bullish = data.bullish_count || 0;
          const bearish = data.bearish_count || 0;
          const total = bullish + bearish;
          const bullPct = total > 0 ? (bullish / total) * 100 : 50;

          return (
            <div key={sym} className="card-base card-hover p-4">
              <div className="flex items-center justify-between mb-3">
                <span className="text-sm font-bold text-white">{sym}</span>
                {data.price > 0 && (
                  <span className="text-xs font-mono text-arena-text-secondary">
                    ${data.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                  </span>
                )}
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between text-[10px]">
                  <span className="text-arena-green">Bullish: {bullish}</span>
                  <span className="text-arena-red">Bearish: {bearish}</span>
                </div>
                <div className="h-2 rounded-full overflow-hidden flex bg-arena-bg">
                  <div className="bg-arena-green" style={{ width: `${bullPct}%` }} />
                  <div className="bg-arena-red" style={{ width: `${100 - bullPct}%` }} />
                </div>
                <div className="text-[10px] text-arena-text-dim">
                  {watching} agent{watching !== 1 ? 's' : ''} watching
                </div>
              </div>

              {data.agent_positions && data.agent_positions.length > 0 && (
                <div className="mt-3 pt-3 border-t border-arena-border space-y-1">
                  {data.agent_positions.map((pos, i) => (
                    <div key={i} className="flex items-center justify-between text-[10px]">
                      <span className="text-white">{pos.agent}</span>
                      <span className={pos.side === 'long' ? 'text-arena-green' : 'text-arena-red'}>
                        {pos.side.toUpperCase()}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {symbols.length === 0 && (
        <div className="flex items-center justify-center h-64 text-arena-text-dim text-sm">
          No market data available. Start agents to see battlefield data.
        </div>
      )}
    </div>
  );
}
