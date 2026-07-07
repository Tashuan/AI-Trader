import { useApi } from './useApi'

export interface NewsItem {
  title: string
  url: string
  source: string
  summary: string
  time_published: string
  overall_sentiment_score: number
  overall_sentiment_label: string
  ticker_sentiment: { ticker: string; relevance_score: number; sentiment_score: number; sentiment_label: string }[]
  topics: { topic: string; relevance_score: number }[]
}

export interface NewsResponse {
  categories: {
    category: string
    label: string
    items: NewsItem[]
  }[]
}

export function useNews(limit = 20, pollInterval?: number) {
  return useApi<NewsResponse>(`/market-intel/news?limit=${limit}`, undefined, pollInterval)
}
