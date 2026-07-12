export function LoadingSpinner({ size = 40 }: { size?: number }) {
  return (
    <div className="loading" style={{ minHeight: size }}>
      <div className="spinner" style={{ width: size, height: size }}></div>
    </div>
  )
}

export function EmptyState({ icon, title, subtitle }: { icon: string; title: string; subtitle?: string }) {
  return (
    <div className="empty-state">
      <div className="empty-state-icon">{icon}</div>
      <div className="empty-state-title">{title}</div>
      {subtitle && <div className="empty-state-subtitle">{subtitle}</div>}
    </div>
  )
}

export function StatCard({
  label,
  value,
  subtitle,
  trend,
}: {
  label: string
  value: string | number
  subtitle?: string
  trend?: 'up' | 'down' | 'neutral'
}) {
  const trendColor = trend === 'up' ? 'var(--success, #22c55e)' : trend === 'down' ? 'var(--danger, #ef4444)' : 'var(--text-primary)'
  return (
    <div className="stat-card">
      <div className="stat-card-label">{label}</div>
      <div className="stat-card-value" style={{ color: trendColor }}>{value}</div>
      {subtitle && <div className="stat-card-subtitle">{subtitle}</div>}
    </div>
  )
}

export function PnLDisplay({ pnl, percentage }: { pnl: number; percentage?: number }) {
  const isPositive = pnl >= 0
  const sign = isPositive ? '+' : ''
  return (
    <span className={`pnl-display ${isPositive ? 'pnl-positive' : 'pnl-negative'}`}>
      {sign}${Math.abs(pnl).toLocaleString(undefined, { maximumFractionDigits: 2 })}
      {percentage !== undefined && (
        <span className="pnl-percentage"> ({sign}{percentage.toFixed(2)}%)</span>
      )}
    </span>
  )
}
