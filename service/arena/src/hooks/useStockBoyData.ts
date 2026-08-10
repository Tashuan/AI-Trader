import { useCallback, useEffect, useState } from 'react';
import type { StockBoySnapshot, StockBoySupervisorStatus } from '../types';

const API_BASE = '/api';
const REFRESH_INTERVAL = 10000;

interface UseStockBoyData {
  snapshot: StockBoySnapshot | null;
  status: StockBoySupervisorStatus | null;
  loading: boolean;
  error: string | null;
  lastUpdated: number | null;
  refresh: () => Promise<void>;
  runControl: (path: string, body?: Record<string, unknown>) => Promise<{ success: boolean; message?: string }>;
}

export function useStockBoyData(enabled = true): UseStockBoyData {
  const [snapshot, setSnapshot] = useState<StockBoySnapshot | null>(null);
  const [status, setStatus] = useState<StockBoySupervisorStatus | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);

  const authHeaders = () => ({
    'Authorization': `Bearer ${localStorage.getItem('auth_token') || ''}`,
  });

  const refresh = useCallback(async () => {
    if (!enabled) return;
    try {
      const headers = authHeaders();
      const [statusResponse, snapshotResponse] = await Promise.all([
        fetch(`${API_BASE}/stockboy/status`, { headers }),
        fetch(`${API_BASE}/stockboy/snapshot`, { headers }),
      ]);
      if (!statusResponse.ok || !snapshotResponse.ok) {
        throw new Error(`StockBoy API unavailable (${statusResponse.status}/${snapshotResponse.status})`);
      }
      const nextStatus = await statusResponse.json() as StockBoySupervisorStatus;
      const nextSnapshot = await snapshotResponse.json() as StockBoySnapshot;
      setStatus(nextStatus);
      setSnapshot(nextSnapshot);
      setError(null);
      setLastUpdated(Date.now());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Failed to load StockBoy data');
    } finally {
      setLoading(false);
    }
  }, [enabled]);

  const runControl = useCallback(async (path: string, body: Record<string, unknown> = {}) => {
    try {
      const response = await fetch(`${API_BASE}${path}`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || result.message || `HTTP ${response.status}`);
      await refresh();
      return { success: result.success !== false, message: result.message };
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'StockBoy control failed';
      setError(message);
      return { success: false, message };
    }
  }, [refresh]);

  useEffect(() => {
    if (!enabled) return;
    refresh();
    const timer = window.setInterval(refresh, REFRESH_INTERVAL);
    return () => window.clearInterval(timer);
  }, [enabled, refresh]);

  return { snapshot, status, loading, error, lastUpdated, refresh, runControl };
}
