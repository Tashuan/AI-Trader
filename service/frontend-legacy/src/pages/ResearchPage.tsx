import { useState } from 'react'
import { useNews } from '../hooks/useNews'
import { NewsList } from '../components/NewsList'
import { TechnicalPanel } from '../components/TechnicalPanel'
import { LoadingSpinner, EmptyState, StatCard } from '../components/Common'

const DEFAULT_SYMBOLS = ['BTC', 'ETH', 'SOL', 'NVDA', 'AAPL', 'TSLA']

export function ResearchPage() {
  const { data, loading, error } = useNews(30, 60000)
  const [selectedSymbol, setSelectedSymbol] = useState<string>('BTC')
  const [filterCategory, setFilterCategory] = useState<string>('all')

  const allNews = data?.categories?.flatMap((cat) =>
    cat.items.map((item) => ({ ...item, category: cat.category }))
  ) || []

  const filteredNews = filterCategory === 'all'
    ? allNews
    : allNews.filter((n) => n.category === filterCategory)

  const categories = data?.categories?.map((c) => c.category) || []
  const bullishCount = allNews.filter((n) => n.overall_sentiment_label?.includes('Bullish')).length
  const bearishCount = allNews.filter((n) => n.overall_sentiment_label?.includes('Bearish')).length
  const neutralCount = allNews.length - bullishCount - bearishCount

  return (
    <div className="research-page">
      <div className="page-header">
        <h2>Research Panel</h2>
        <span className="page-subtitle">News sentiment + technical analysis</span>
      </div>

      <div className="research-stats">
        <StatCard label="Total Stories" value={allNews.length} />
        <StatCard label="Bullish" value={bullishCount} trend="up" />
        <StatCard label="Bearish" value={bearishCount} trend="down" />
        <StatCard label="Neutral" value={neutralCount} trend="neutral" />
      </div>

      <div className="research-layout">
        <div className="research-left">
          <div className="research-section-header">
            <h3>News Feed</h3>
            <select
              className="category-filter"
              value={filterCategory}
              onChange={(e) => setFilterCategory(e.target.value)}
            >
              <option value="all">All Categories</option>
              {categories.map((cat) => (
                <option key={cat} value={cat}>{cat}</option>
              ))}
            </select>
          </div>
          {loading ? <LoadingSpinner size={30} /> : error ? <EmptyState icon="⚠️" title="Failed to load news" /> : <NewsList items={filteredNews} onSelectTicker={setSelectedSymbol} />}
        </div>

        <div className="research-right">
          <div className="research-section-header">
            <h3>Technical Analysis</h3>
            <div className="symbol-selector">
              {DEFAULT_SYMBOLS.map((sym) => (
                <button
                  key={sym}
                  className={`symbol-btn ${selectedSymbol === sym ? 'active' : ''}`}
                  onClick={() => setSelectedSymbol(sym)}
                >
                  {sym}
                </button>
              ))}
            </div>
          </div>
          <TechnicalPanel symbol={selectedSymbol} />
        </div>
      </div>
    </div>
  )
}
