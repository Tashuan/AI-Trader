import { useMemo, useState } from 'react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

interface ProfitPoint {
  recorded_at: string;
  total_value: number;
  profit?: number;
}

interface GrowthChartProps {
  data: ProfitPoint[];
  height?: number;
}

type Metric = 'value' | 'return';
type Range = '7d' | '30d' | 'all';

function formatDate(ts: string) {
  const d = new Date(ts);
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function formatCurrency(value: number) {
  return `$${value.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
}

function formatPercent(value: number) {
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
}

export function GrowthChart({ data, height = 240 }: GrowthChartProps) {
  const [metric, setMetric] = useState<Metric>('value');
  const [range, setRange] = useState<Range>('all');
  const [chartType, setChartType] = useState<'area' | 'line'>('area');

  const processed = useMemo(() => {
    const sorted = [...data]
      .filter((p) => p && typeof p.total_value === 'number')
      .sort((a, b) => new Date(a.recorded_at).getTime() - new Date(b.recorded_at).getTime());

    if (sorted.length === 0) return [];

    const now = Date.now();
    const rangeMs = range === '7d' ? 7 * 24 * 60 * 60 * 1000 : range === '30d' ? 30 * 24 * 60 * 60 * 1000 : Infinity;
    const filtered = sorted.filter((p) => now - new Date(p.recorded_at).getTime() <= rangeMs);

    const startValue = sorted[0]?.total_value || 1;
    const effective = filtered.length > 1 ? filtered : sorted;

    return effective.map((p) => ({
      date: formatDate(p.recorded_at),
      recorded_at: p.recorded_at,
      value: p.total_value,
      return: ((p.total_value - startValue) / Math.max(startValue, 1)) * 100,
    }));
  }, [data, range]);

  const startValue = processed[0]?.value;
  const endValue = processed[processed.length - 1]?.value;
  const overallReturn = processed.length > 1 ? processed[processed.length - 1].return : 0;
  const isPositive = overallReturn >= 0;
  const color = isPositive ? '#10B981' : '#EF4444';

  if (processed.length < 2) {
    return (
      <div className="h-60 flex items-center justify-center text-arena-text-dim text-xs">
        Not enough data
      </div>
    );
  }

  const Chart = chartType === 'area' ? AreaChart : LineChart;
  const DataViz =
    chartType === 'area' ? (
      <Area
        type="monotone"
        dataKey={metric}
        stroke={color}
        strokeWidth={2}
        fill={color}
        fillOpacity={0.12}
        dot={false}
        activeDot={{ r: 4 }}
      />
    ) : (
      <Line
        type="monotone"
        dataKey={metric}
        stroke={color}
        strokeWidth={2}
        dot={false}
        activeDot={{ r: 4 }}
      />
    );

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-2">
        <div className="flex items-baseline gap-2 flex-wrap">
          <span className="text-xl font-bold text-white">{formatCurrency(endValue ?? 0)}</span>
          <span className={`text-sm font-semibold ${isPositive ? 'text-arena-green' : 'text-arena-red'}`}>
            {formatPercent(overallReturn)} from {formatCurrency(startValue ?? 0)}
          </span>
        </div>

        <div className="flex flex-wrap gap-2">
          <div className="inline-flex bg-arena-bg rounded-lg border border-arena-border overflow-hidden">
            <button
              className={`px-2.5 py-1 text-[10px] font-medium transition-colors ${metric === 'value' ? 'bg-arena-purple text-white' : 'text-arena-text-dim hover:text-white'}`}
              onClick={() => setMetric('value')}
            >
              Value
            </button>
            <button
              className={`px-2.5 py-1 text-[10px] font-medium transition-colors ${metric === 'return' ? 'bg-arena-purple text-white' : 'text-arena-text-dim hover:text-white'}`}
              onClick={() => setMetric('return')}
            >
              Return %
            </button>
          </div>

          <div className="inline-flex bg-arena-bg rounded-lg border border-arena-border overflow-hidden">
            {(['7d', '30d', 'all'] as Range[]).map((r) => (
              <button
                key={r}
                className={`px-2.5 py-1 text-[10px] font-medium transition-colors ${range === r ? 'bg-arena-purple text-white' : 'text-arena-text-dim hover:text-white'}`}
                onClick={() => setRange(r)}
              >
                {r === '7d' ? '7D' : r === '30d' ? '30D' : 'All'}
              </button>
            ))}
          </div>

          <div className="inline-flex bg-arena-bg rounded-lg border border-arena-border overflow-hidden">
            <button
              className={`px-2.5 py-1 text-[10px] font-medium transition-colors ${chartType === 'area' ? 'bg-arena-purple text-white' : 'text-arena-text-dim hover:text-white'}`}
              onClick={() => setChartType('area')}
            >
              Area
            </button>
            <button
              className={`px-2.5 py-1 text-[10px] font-medium transition-colors ${chartType === 'line' ? 'bg-arena-purple text-white' : 'text-arena-text-dim hover:text-white'}`}
              onClick={() => setChartType('line')}
            >
              Line
            </button>
          </div>
        </div>
      </div>

      <div style={{ height, minHeight: height }}>
        <ResponsiveContainer width="100%" height="100%">
          <Chart data={processed} margin={{ top: 5, right: 10, left: -10, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" vertical={false} />
            <XAxis dataKey="date" stroke="#6B7280" tick={{ fontSize: 10 }} minTickGap={24} />
            <YAxis
              stroke="#6B7280"
              tick={{ fontSize: 10 }}
              tickFormatter={(v: number) => (metric === 'return' ? `${v.toFixed(0)}%` : `$${(v / 1000).toFixed(1)}k`)}
              width={55}
              domain={metric === 'return' ? ['auto', 'auto'] : undefined}
            />
            <Tooltip
              contentStyle={{
                background: '#10141B',
                border: '1px solid rgba(255,255,255,0.08)',
                borderRadius: '8px',
                fontSize: '12px',
              }}
              labelStyle={{ color: '#9CA3AF' }}
              formatter={(value: any) => (metric === 'return' ? formatPercent(Number(value)) : formatCurrency(Number(value)))}
              labelFormatter={(label: any) => String(label)}
            />
            {DataViz}
          </Chart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
