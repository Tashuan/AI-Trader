import { useState, useEffect, useCallback } from 'react';
import { Shield } from 'lucide-react';
import { PortfolioRiskPanel } from '../components/PortfolioRiskPanel';
import type { PortfolioRiskData, UserInfo } from '../types';

const REFRESH_INTERVAL = 30000;

export function RiskPage() {
  const [data, setData] = useState<PortfolioRiskData | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const [userInfo, setUserInfo] = useState<UserInfo | null>(null);

  const fetchRisk = useCallback(async () => {
    try {
      const resp = await fetch('/api/arena/portfolio-risk');
      if (!resp.ok) return;
      const json = await resp.json();
      setData(json);
      setLastUpdated(Date.now());
    } catch {
      // silent fail
    }
  }, []);

  const fetchUserInfo = useCallback(async () => {
    try {
      const resp = await fetch('/api/arena/me', {
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('auth_token') || ''}`,
        },
      });
      if (!resp.ok) return;
      const json = await resp.json();
      setUserInfo(json);
    } catch {
      // silent fail
    }
  }, []);

  useEffect(() => {
    fetchRisk();
    fetchUserInfo();
    const interval = setInterval(fetchRisk, REFRESH_INTERVAL);
    return () => clearInterval(interval);
  }, [fetchRisk, fetchUserInfo]);

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="flex items-center gap-2 mb-1">
        <Shield size={16} className="text-arena-purple" />
        <h1 className="text-base font-bold text-white">Portfolio Risk Engine</h1>
      </div>
      <p className="text-[11px] text-arena-text-dim mb-6">
        Cross-agent portfolio risk monitoring — symbol concentration, sector exposure, crowding, and daily loss limits.
      </p>

      <div className="max-w-2xl">
        <PortfolioRiskPanel
          data={data}
          isAdmin={userInfo?.is_admin ?? false}
          lastUpdated={lastUpdated}
          onUnhalt={async () => {
            try {
              const resp = await fetch('/api/arena/portfolio-risk/unhalt', {
                method: 'POST',
                headers: {
                  'Authorization': `Bearer ${localStorage.getItem('auth_token') || ''}`,
                },
              });
              if (resp.ok) {
                fetchRisk();
              }
            } catch {
              // silent fail
            }
          }}
        />
      </div>
    </div>
  );
}
