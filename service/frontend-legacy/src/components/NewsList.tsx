import type { NewsItem } from '../hooks/useNews'

interface NewsListProps {
  items: NewsItem[]
  onSelectTicker?: (ticker: string) => void
}

function sentimentColor(score: number): string {
  if (score > 0.15) return 'var(--success, #22c55e)'
  if (score < -0.15) return 'var(--danger, #ef4444)'
  return 'var(--text-muted)'
}

function sentimentBg(score: number): string {
  if (score > 0.15) return 'rgba(34, 197, 94, 0.1)'
  if (score < -0.15) return 'rgba(239, 68, 68, 0.1)'
  return 'rgba(128, 128, 128, 0.05)'
}

export function NewsList({ items, onSelectTicker }: NewsListProps) {
  if (!items.length) {
    return <div className="empty-state"><div className="empty-state-icon">📰</div><div className="empty-state-title">No news available</div></div>
  }

  return (
    <div className="news-list">
      {items.map((item, i) => (
        <div
          key={i}
          className="news-item"
          style={{ borderLeftColor: sentimentColor(item.overall_sentiment_score) }}
        >
          <div className="news-item-header">
            <span
              className="news-sentiment-badge"
              style={{ background: sentimentBg(item.overall_sentiment_score), color: sentimentColor(item.overall_sentiment_score) }}
            >
              {item.overall_sentiment_label} {item.overall_sentiment_score > 0 ? '+' : ''}{item.overall_sentiment_score.toFixed(2)}
            </span>
            <span className="news-source">{item.source}</span>
          </div>
          <div className="news-title">{item.title}</div>
          {item.summary && <div className="news-summary">{item.summary.slice(0, 200)}{item.summary.length > 200 ? '...' : ''}</div>}
          {item.ticker_sentiment?.length > 0 && (
            <div className="news-tickers">
              {item.ticker_sentiment.map((ts, j) => (
                <button
                  key={j}
                  className="ticker-chip"
                  onClick={() => onSelectTicker?.(ts.ticker.replace('CRYPTO:', '').replace('FOREX:', ''))}
                >
                  {ts.ticker.replace('CRYPTO:', '').replace('FOREX:', '')}
                </button>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
