import { useState, useEffect, useCallback } from 'react';
import {
  AreaChart, Area, LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, BarChart, Bar, Cell,
} from 'recharts';
import { Play, Loader2, TrendingUp, TrendingDown, FlaskConical, Zap, Calendar, Stethoscope, AlertTriangle, CheckCircle, XCircle, Lightbulb, Copy, Check, Sparkles, Rocket } from 'lucide-react';

interface Strategy {
  key: string;
  name: string;
  tagline: string;
  strategy_type: string;
  watchlist: string[];
  risk_tolerance: string;
  hold_period: string;
}

interface TradeRecord {
  symbol: string;
  side: string;
  entry_date: string;
  exit_date: string;
  entry_price: number;
  exit_price: number;
  quantity: number;
  pnl: number;
  pnl_pct: number;
  hold_days: number;
  hold_hours: number;
  reason: string;
}

interface BacktestReport {
  agent_name: string;
  symbols: string[];
  start_date: string;
  end_date: string;
  initial_capital: number;
  final_equity: number;
  total_return_pct: number;
  sharpe_ratio: number;
  max_drawdown_pct: number;
  win_rate: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  avg_hold_days: number;
  avg_hold_hours?: number;
  profit_factor: number;
  equity_curve: { date: string; equity: number }[];
  trades: TradeRecord[];
  per_symbol_stats: Record<string, {
    trades: number;
    wins: number;
    win_rate: number;
    total_pnl: number;
    avg_pnl_pct: number;
  }>;
  interval: string;
  slippage_bps: number;
  activation_gate?: { eligible: boolean; checks: Record<string, boolean> };
  diagnostics?: {
    scan_bars?: number;
    mtf_qualified?: number;
    entry_rejected?: number;
    trend_rejected?: number;
    setup_qualified?: number;
    orders_placed?: number;
    orders_filled?: number;
    orders_expired?: number;
    pending_at_end?: number;
    same_bar_exit_skipped?: number;
    exit_counts?: Record<string, number>;
    sample_warning?: string;
  };
}

export function BacktestPage() {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [selectedKey, setSelectedKey] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [symbolsInput, setSymbolsInput] = useState('');
  const [capital, setCapital] = useState('10000');
  const [running, setRunning] = useState(false);
  const [signalAnalysis, setSignalAnalysis] = useState<Record<string, any> | null>(null);
  const [analysisRunning, setAnalysisRunning] = useState(false);
  const [report, setReport] = useState<BacktestReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [strategiesLoading, setStrategiesLoading] = useState(true);
  const [activePreset, setActivePreset] = useState<string>('');
  const [copied, setCopied] = useState(false);
  const [llmDiagnosis, setLlmDiagnosis] = useState<string | null>(null);
  const [llmLoading, setLlmLoading] = useState(false);
  const [llmAvailable, setLlmAvailable] = useState<boolean | null>(null);
  const [candleInterval, setCandleInterval] = useState('1d');
  const [slippageBps, setSlippageBps] = useState('5');
  const [goalTarget, setGoalTarget] = useState('');
  const [activeTestPreset, setActiveTestPreset] = useState('');
  const [paramsOverride, setParamsOverride] = useState<Record<string, unknown> | null>(null);
  const [walkForwardRunning, setWalkForwardRunning] = useState(false);
  const [walkForwardResult, setWalkForwardResult] = useState<Record<string, any> | null>(null);

  interface TestPreset {
    id: string;
    label: string;
    description: string;
    agentKey: string;
    startDate: string;
    endDate: string;
    symbols: string;
    capital: string;
    interval: string;
    slippage: string;
    goalTarget: string;
    paramsOverride?: Record<string, unknown>;
  }

  const testPresets: TestPreset[] = [
    // ---- CryptoRunner (4h candles, regime-aware crypto swing) ----
    {
      id: 'crypto-3m',
      label: 'CryptoRunner 3-Month',
      description: 'Fast iteration loop. 4h candles over the last quarter on the full liquid crypto watchlist with daily confirmation and ATR exits.',
      agentKey: 'cryptorunner',
      startDate: new Date(new Date().setMonth(new Date().getMonth() - 3)).toISOString().split('T')[0],
      endDate: new Date().toISOString().split('T')[0],
      symbols: '',
      capital: '10000',
      interval: '4h',
      slippage: '5',
      goalTarget: '',
    },
    {
      id: 'crypto-1y',
      label: 'CryptoRunner 1-Year',
      description: 'The statistically meaningful full replay. 4h candles over a full year on the full watchlist at the $10K account budget — enough trades for stable Sharpe and drawdown readings.',
      agentKey: 'cryptorunner',
      startDate: new Date(new Date().setFullYear(new Date().getFullYear() - 1)).toISOString().split('T')[0],
      endDate: new Date().toISOString().split('T')[0],
      symbols: '',
      capital: '10000',
      interval: '4h',
      slippage: '5',
      goalTarget: '',
    },
    {
      id: 'crypto-btc-sol-6m',
      label: 'CryptoRunner BTC/SOL 6-Month',
      description: 'Isolate the two most liquid names to evaluate signal quality and regime filtering without altcoin correlation noise. 4h candles over 6 months.',
      agentKey: 'cryptorunner',
      startDate: new Date(new Date().setMonth(new Date().getMonth() - 6)).toISOString().split('T')[0],
      endDate: new Date().toISOString().split('T')[0],
      symbols: 'BTC,SOL',
      capital: '10000',
      interval: '4h',
      slippage: '5',
      goalTarget: '',
    },
    {
      id: 'crypto-wf-profile-a',
      label: 'CryptoRunner Walk-Forward Profile A',
      description: 'Out-of-sample validation. 90-day train / 30-day test windows rolled over a full year on BTC, ETH, SOL. Confirms regime-aware parameters hold up beyond the in-sample window.',
      agentKey: 'cryptorunner',
      startDate: new Date(new Date().setFullYear(new Date().getFullYear() - 1)).toISOString().split('T')[0],
      endDate: new Date().toISOString().split('T')[0],
      symbols: 'BTC,ETH,SOL',
      capital: '10000',
      interval: '4h',
      slippage: '5',
      goalTarget: '',
    },
    // ---- BlitzRunner (1h candles, equity momentum scalp) ----
    {
      id: 'runner-equity-3m',
      label: 'BlitzRunner 3-Month',
      description: 'Fast iteration loop. 1h candles over the last quarter on the default equity watchlist (NVDA, TSLA, META, AMZN) using the deterministic runner profile.',
      agentKey: 'blitzrunner',
      startDate: new Date(new Date().setMonth(new Date().getMonth() - 3)).toISOString().split('T')[0],
      endDate: new Date().toISOString().split('T')[0],
      symbols: '',
      capital: '10000',
      interval: '1h',
      slippage: '5',
      goalTarget: '',
    },
    {
      id: 'runner-equity-1y',
      label: 'BlitzRunner 1-Year',
      description: 'Full-year replay on the default equity watchlist with 1h candles. Enough trades for a stable win-rate, Sharpe, and max-drawdown read on the scalp profile.',
      agentKey: 'blitzrunner',
      startDate: new Date(new Date().setFullYear(new Date().getFullYear() - 1)).toISOString().split('T')[0],
      endDate: new Date().toISOString().split('T')[0],
      symbols: '',
      capital: '10000',
      interval: '1h',
      slippage: '5',
      goalTarget: '',
    },
    {
      id: 'runner-meta-focus',
      label: 'BlitzRunner META Focus',
      description: 'Single-name isolation on META with 1h candles over 3 months. Removes cross-symbol noise to grade entry/exit quality on one name — META is a verified high-trade-count symbol.',
      agentKey: 'blitzrunner',
      startDate: new Date(new Date().setMonth(new Date().getMonth() - 3)).toISOString().split('T')[0],
      endDate: new Date().toISOString().split('T')[0],
      symbols: 'META',
      capital: '10000',
      interval: '1h',
      slippage: '5',
      goalTarget: '',
    },
    {
      id: 'runner-nvda-tsla-6m',
      label: 'BlitzRunner NVDA/TSLA 6-Month',
      description: 'High-beta pair stress test. NVDA and TSLA over 6 months on 1h candles — the most volatile names in the watchlist, ideal for probing momentum capture and TP/SL tightness.',
      agentKey: 'blitzrunner',
      startDate: new Date(new Date().setMonth(new Date().getMonth() - 6)).toISOString().split('T')[0],
      endDate: new Date().toISOString().split('T')[0],
      symbols: 'NVDA,TSLA',
      capital: '10000',
      interval: '1h',
      slippage: '5',
      goalTarget: '',
    },
    // ---- ScalpRunner (5m candles from cached Alpaca data, 4-step scalp) ----
    {
      id: 'scalp-runner-1mo',
      label: 'ScalpRunner 1-Month (Cached)',
      description: '5m replay over a 1-month slice of cached Alpaca data on the default equity watchlist. Exercises the full 4-step pipeline: discovery, liquidity filter, multi-TF analysis, and stop-limit fills. No Schwab auth required.',
      agentKey: 'scalprunner',
      startDate: '2025-08-04',
      endDate: '2025-09-04',
      symbols: '',
      capital: '10000',
      interval: '5m',
      slippage: '2',
      goalTarget: '',
      paramsOverride: {
        entry_criteria: { min_vol_ratio: 1.5, min_signals: 3 },
        position_sizing: { max_positions: 2, max_pending_orders: 2 },
        order: { entry_trigger_offset_pct: 0.08, sl_atr_multiple: 1.0, tp_atr_multiple: 1.5 },
      },
    },
    {
      id: 'scalp-runner-3mo',
      label: 'ScalpRunner 3-Month (Cached)',
      description: '5m replay over a 3-month slice of cached Alpaca data on the full equity watchlist. Enough trades for a meaningful win-rate and Sharpe read on the scalp profile.',
      agentKey: 'scalprunner',
      startDate: '2025-08-04',
      endDate: '2025-11-04',
      symbols: '',
      capital: '10000',
      interval: '5m',
      slippage: '2',
      goalTarget: '',
      paramsOverride: {
        entry_criteria: { min_vol_ratio: 1.75, min_signals: 4, require_trend_agreement: true },
        position_sizing: { max_positions: 1, max_pending_orders: 2 },
        order: { entry_trigger_offset_pct: 0.10, sl_atr_multiple: 1.2, tp_atr_multiple: 1.8 },
      },
    },
    {
      id: 'scalp-runner-full-year',
      label: 'ScalpRunner Full Year (Cached)',
      description: 'The full cached replay. 5m bars over the entire ~1yr Alpaca cache (Aug 2025 – Aug 2026) on the default watchlist. Maximum trade count for stable Sharpe and drawdown readings.',
      agentKey: 'scalprunner',
      startDate: '2025-08-04',
      endDate: '2026-08-09',
      symbols: '',
      capital: '10000',
      interval: '5m',
      slippage: '2',
      goalTarget: '',
      paramsOverride: {
        entry_criteria: { min_vol_ratio: 1.25, min_signals: 3, require_trend_agreement: true },
        position_sizing: { max_positions: 2, max_pending_orders: 2 },
        order: { entry_trigger_offset_pct: 0.05, sl_atr_multiple: 1.2, tp_atr_multiple: 2.0 },
      },
    },
    {
      id: 'scalp-runner-nvda',
      label: 'ScalpRunner NVDA Focus (Cached)',
      description: 'Single-name isolation on NVDA over the full cached year at 5m resolution. Removes cross-symbol noise to grade entry/exit quality on the highest-volume scalp name.',
      agentKey: 'scalprunner',
      startDate: '2025-08-04',
      endDate: '2026-08-09',
      symbols: 'NVDA',
      capital: '10000',
      interval: '5m',
      slippage: '2',
      goalTarget: '',
    },
  ];

  const applyTestPreset = (preset: TestPreset) => {
    setActiveTestPreset(preset.id);
    setSelectedKey(preset.agentKey);
    setStartDate(preset.startDate);
    setEndDate(preset.endDate);
    setSymbolsInput(preset.symbols);
    setCapital(preset.capital);
    setCandleInterval(preset.interval);
    setSlippageBps(preset.slippage);
    setGoalTarget(preset.goalTarget);
    setParamsOverride(preset.paramsOverride || null);
    setSignalAnalysis(null);
    setActivePreset('');
  };

  const applyPreset = (preset: string) => {
    setActivePreset(preset);
    const now = new Date();
    const start = new Date();
    switch (preset) {
      case '1M': start.setMonth(now.getMonth() - 1); break;
      case '3M': start.setMonth(now.getMonth() - 3); break;
      case '6M': start.setMonth(now.getMonth() - 6); break;
      case '1Y': start.setFullYear(now.getFullYear() - 1); break;
      case '2Y': start.setFullYear(now.getFullYear() - 2); break;
      case 'YTD': start.setMonth(0); start.setDate(1); break;
      default: return;
    }
    setStartDate(start.toISOString().split('T')[0]);
    setEndDate(now.toISOString().split('T')[0]);
  };

  const runWalkForward = async () => {
    if (!selectedKey || !startDate || !endDate) return;
    setWalkForwardRunning(true);
    setWalkForwardResult(null);
    setError(null);
    try {
      const symbols = symbolsInput
        ? symbolsInput.split(',').map(s => s.trim()).filter(Boolean)
        : (selectedStrategy?.watchlist || []);
      const res = await fetch('/api/backtest/walk-forward', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          agent_key: selectedKey,
          symbols,
          start_date: startDate,
          end_date: endDate,
          candidates: { baseline: {} },
          train_days: 90,
          test_days: 30,
          step_days: 30,
          initial_capital: parseFloat(capital) || 10000,
          interval: candleInterval,
          slippage_bps: parseFloat(slippageBps) || 5,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setWalkForwardResult(data.summary || null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Walk-forward failed');
    } finally {
      setWalkForwardRunning(false);
    }
  };

  const fetchStrategies = useCallback(async () => {
    try {
      const resp = await fetch('/api/backtest/strategies');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      let list = data.strategies || [];
      // Sort: blitztrader first, then alphabetical
      list = list.sort((a: Strategy, b: Strategy) => {
        const order = ['blitzrunner', 'scalprunner', 'cryptorunner', 'blitztrader'];
        const ai = order.indexOf(a.key);
        const bi = order.indexOf(b.key);
        if (ai !== -1 || bi !== -1) return (ai === -1 ? order.length : ai) - (bi === -1 ? order.length : bi);
        return a.name.localeCompare(b.name);
      });
      setStrategies(list);
      if (list.length > 0 && !selectedKey) {
        setSelectedKey('blitzrunner');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load strategies');
    } finally {
      setStrategiesLoading(false);
    }
  }, [selectedKey]);

  useEffect(() => {
    fetchStrategies();
  }, [fetchStrategies]);

  const selectedStrategy = strategies.find(s => s.key === selectedKey);

  useEffect(() => {
    if (!selectedStrategy) return;
    if (selectedKey === 'cryptorunner') {
      setCandleInterval('4h');
      return;
    }
    if (selectedKey === 'blitzrunner') {
      setCandleInterval('1h');
      return;
    }
    if (selectedKey === 'scalprunner') {
      setCandleInterval('5m');
      return;
    }
    const hp = selectedStrategy.hold_period;
    if (hp === 'scalp') {
      setCandleInterval('15m');
    } else if (hp === 'intraday') {
      setCandleInterval('1h');
    } else {
      setCandleInterval('1d');
    }
  }, [selectedStrategy]);

  const handleRun = async () => {
    if (!selectedKey) return;
    setRunning(true);
    setError(null);
    setReport(null);

    try {
      const body: Record<string, unknown> = {
        agent_key: selectedKey,
        start_date: startDate,
        end_date: endDate,
        initial_capital: parseFloat(capital) || 10000,
      };
      if (symbolsInput.trim()) {
        body.symbols = symbolsInput.split(',').map(s => s.trim().toUpperCase()).filter(Boolean);
      }
      body.interval = candleInterval;
      body.slippage_bps = parseFloat(slippageBps) || 0;
      if (goalTarget.trim()) {
        body.goal_target = parseFloat(goalTarget) || undefined;
      }
      if (paramsOverride) {
        body.params_override = paramsOverride;
      }

      const resp = await fetch('/api/backtest/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        throw Error(errData.detail || `HTTP ${resp.status}`);
      }

      const data = await resp.json();
      setReport(data.report);
      setLlmDiagnosis(null);
      setLlmAvailable(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Backtest failed');
    } finally {
      setRunning(false);
    }
  };

  const handleAnalyzeSignals = async () => {
    if (selectedKey !== 'scalprunner' || !startDate || !endDate) return;
    setAnalysisRunning(true);
    setError(null);
    try {
      const body: Record<string, unknown> = {
        symbols: symbolsInput.trim()
          ? symbolsInput.split(',').map(s => s.trim().toUpperCase()).filter(Boolean)
          : (selectedStrategy?.watchlist || []),
        start_date: startDate,
        end_date: endDate,
        interval: '5m',
        horizons: [1, 3, 6, 12],
        cooldown_bars: 6,
      };
      if (paramsOverride) body.params_override = paramsOverride;
      const resp = await fetch('/api/backtest/scalp-analysis', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        throw Error(errData.detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      setSignalAnalysis(data.analysis || null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Signal analysis failed');
    } finally {
      setAnalysisRunning(false);
    }
  };

  const fmtCurrency = (v: number) =>
    `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

  const fmtPct = (v: number, withSign = false) =>
    `${v > 0 && withSign ? '+' : ''}${v.toFixed(2)}%`;

  const equityData = report?.equity_curve.map((p, i) => ({
    idx: i,
    date: p.date,
    equity: p.equity,
  })) || [];

  const symbolStatsData = report
    ? Object.entries(report.per_symbol_stats).map(([sym, s]) => ({
        symbol: sym,
        ...s,
      }))
    : [];

  type DiagnosisLevel = 'good' | 'warning' | 'critical';
  interface DiagnosisItem {
    level: DiagnosisLevel;
    title: string;
    detail: string;
    recommendation: string;
    param?: string;
  }

  const generateDiagnosis = (r: BacktestReport): DiagnosisItem[] => {
    const findings: DiagnosisItem[] = [];
    const winRatePct = r.win_rate * 100;
    const startMs = Date.parse(r.start_date);
    const endMs = Date.parse(r.end_date);
    const days = Number.isFinite(startMs) && Number.isFinite(endMs)
      ? Math.max(1, Math.ceil((endMs - startMs) / 86400000))
      : Math.max(1, r.equity_curve.length);
    const tradesPerDay = r.total_trades / days;
    const avgWinPnl = r.winning_trades > 0
      ? r.trades.filter(t => t.pnl > 0).reduce((s, t) => s + t.pnl, 0) / r.winning_trades : 0;
    const avgLossPnl = r.losing_trades > 0
      ? Math.abs(r.trades.filter(t => t.pnl < 0).reduce((s, t) => s + t.pnl, 0) / r.losing_trades) : 0;
    const bestSymbol = symbolStatsData.length > 0
      ? symbolStatsData.reduce((a, b) => a.total_pnl > b.total_pnl ? a : b) : null;
    const worstSymbol = symbolStatsData.length > 0
      ? symbolStatsData.reduce((a, b) => a.total_pnl < b.total_pnl ? a : b) : null;

    // --- OVERALL PROFITABILITY ---
    if (r.total_return_pct > 10) {
      findings.push({
        level: 'good', title: 'Strong overall returns',
        detail: `Strategy returned ${fmtPct(r.total_return_pct, true)} over ${days} days.`,
        recommendation: 'Current parameters are working well. Consider running a longer backtest to confirm stability.',
      });
    } else if (r.total_return_pct > 0) {
      findings.push({
        level: 'warning', title: 'Marginally profitable',
        detail: `Strategy returned only ${fmtPct(r.total_return_pct, true)}. Barely beating cash.`,
        recommendation: 'Look at individual metrics below to find what to tune. Small improvements to TP/SL or sizing can compound significantly.',
      });
    } else {
      findings.push({
        level: 'critical', title: 'Strategy is losing money',
        detail: `Strategy returned ${fmtPct(r.total_return_pct, true)} over ${days} days. Capital is being eroded.`,
        recommendation: 'Do not deploy live without changes. Focus on the critical findings below before running this strategy.',
      });
    }

    // --- WIN RATE ---
    if (winRatePct >= 60 && r.total_trades >= 10) {
      findings.push({
        level: 'good', title: 'High win rate',
        detail: `${winRatePct.toFixed(1)}% of trades are winners (${r.winning_trades}W / ${r.losing_trades}L).`,
        recommendation: 'Good entry selection. If returns are still low, your TP may be too tight — you\'re cutting winners early. Try increasing PROFIT_TARGET_PCT.',
        param: 'PROFIT_TARGET_PCT',
      });
    } else if (winRatePct >= 45 && r.total_trades >= 10) {
      findings.push({
        level: 'warning', title: 'Moderate win rate',
        detail: `${winRatePct.toFixed(1)}% win rate. Near break-even on trade selection.`,
        recommendation: 'Entry signals are decent but not great. Consider tightening the momentum signal threshold (require 5 instead of 4 signals) to be more selective.',
        param: 'Signal threshold in _is_moving_fast()',
      });
    } else if (r.total_trades >= 10) {
      findings.push({
        level: 'critical', title: 'Low win rate',
        detail: `Only ${winRatePct.toFixed(1)}% of trades win. Most entries are wrong.`,
        recommendation: 'Entry logic is too loose. Either raise confidence_threshold to be more selective, or increase the momentum signal threshold. Fewer, higher-quality trades will perform better.',
        param: 'confidence_threshold / signal threshold',
      });
    }

    // --- PROFIT FACTOR ---
    if (r.profit_factor >= 1.5 && r.total_trades >= 10) {
      findings.push({
        level: 'good', title: 'Healthy profit factor',
        detail: `Profit factor ${r.profit_factor.toFixed(2)} — winners generate ${r.profit_factor.toFixed(1)}× more than losers lose.`,
        recommendation: 'Risk/reward is solid. Maintain current TP/SL levels.',
      });
    } else if (r.profit_factor >= 1.0 && r.total_trades >= 10) {
      findings.push({
        level: 'warning', title: 'Marginal profit factor',
        detail: `Profit factor ${r.profit_factor.toFixed(2)} — barely profitable. Winners barely exceed losers.`,
        recommendation: avgWinPnl < avgLossPnl
          ? `Average win ($${avgWinPnl.toFixed(0)}) is smaller than average loss ($${avgLossPnl.toFixed(0)}). Widen PROFIT_TARGET_PCT to let winners run, or tighten STOP_LOSS_PCT to cut losers faster.`
          : `Win/loss sizes are balanced but edge is thin. Consider being more selective with entries to improve win rate.`,
        param: 'PROFIT_TARGET_PCT / STOP_LOSS_PCT',
      });
    } else if (r.total_trades >= 10) {
      findings.push({
        level: 'critical', title: 'Profit factor below 1.0',
        detail: `Profit factor ${r.profit_factor.toFixed(2)} — losers outweigh winners. Strategy loses money per trade on average.`,
        recommendation: avgWinPnl < avgLossPnl
          ? `Average loss ($${avgLossPnl.toFixed(0)}) exceeds average win ($${avgWinPnl.toFixed(0)}). Tighten STOP_LOSS_PCT immediately and widen PROFIT_TARGET_PCT to improve risk/reward.`
          : `Both win rate and sizing need work. Start by reducing position_sizing to limit damage while you fix entry logic.`,
        param: 'STOP_LOSS_PCT / PROFIT_TARGET_PCT / position_sizing',
      });
    }

    // --- MAX DRAWDOWN ---
    if (r.max_drawdown_pct > 20) {
      findings.push({
        level: 'critical', title: 'Severe drawdown',
        detail: `Max drawdown of ${fmtPct(r.max_drawdown_pct)}. This means the strategy at one point lost over 20% of peak equity.`,
        recommendation: 'Position sizing is too aggressive. Reduce position_sizing (e.g., from "yolo" to "large") or lower conviction_multiplier. A 20%+ drawdown is hard to recover from psychologically.',
        param: 'position_sizing / conviction_multiplier',
      });
    } else if (r.max_drawdown_pct > 10) {
      findings.push({
        level: 'warning', title: 'Notable drawdown',
        detail: `Max drawdown of ${fmtPct(r.max_drawdown_pct)}. Significant equity swings.`,
        recommendation: 'Consider reducing position_sizing slightly or adding more symbols to the watchlist for diversification. Fewer concentrated bets will smooth the curve.',
        param: 'position_sizing / watchlist',
      });
    } else if (r.total_trades >= 10) {
      findings.push({
        level: 'good', title: 'Controlled drawdown',
        detail: `Max drawdown only ${fmtPct(r.max_drawdown_pct)}. Equity curve is relatively smooth.`,
        recommendation: 'Risk management is working. You could potentially increase position_sizing slightly if returns are low.',
      });
    }

    // --- SHARPE RATIO ---
    if (r.sharpe_ratio >= 1.5 && r.total_trades >= 10) {
      findings.push({
        level: 'good', title: 'Excellent risk-adjusted returns',
        detail: `Sharpe ratio ${r.sharpe_ratio.toFixed(2)} — strong, consistent returns relative to volatility.`,
        recommendation: 'This is institutional-grade risk-adjusted performance. Maintain current parameters.',
      });
    } else if (r.sharpe_ratio >= 0.5 && r.total_trades >= 10) {
      findings.push({
        level: 'warning', title: 'Mediocre Sharpe ratio',
        detail: `Sharpe ratio ${r.sharpe_ratio.toFixed(2)} — returns are volatile relative to the payoff.`,
        recommendation: 'Returns are inconsistent. Focus on reducing volatility — either diversify the watchlist or reduce position sizing to smooth returns.',
        param: 'watchlist / position_sizing',
      });
    } else if (r.total_trades >= 10) {
      findings.push({
        level: 'critical', title: 'Poor risk-adjusted returns',
        detail: `Sharpe ratio ${r.sharpe_ratio.toFixed(2)} — high volatility for minimal payoff.`,
        recommendation: 'The strategy is taking too much risk for too little return. Reduce position_sizing and focus on improving entry selectivity.',
        param: 'position_sizing / confidence_threshold',
      });
    }

    // --- TRADE FREQUENCY ---
    if (r.total_trades === 0) {
      findings.push({
        level: 'critical', title: 'No trades generated',
        detail: 'The strategy never triggered any entries in this period.',
        recommendation: 'Entry conditions are too strict. Lower confidence_threshold, reduce the momentum signal threshold, or widen the date range. The strategy needs to actually trade to be evaluated.',
        param: 'confidence_threshold / signal threshold',
      });
    } else if (tradesPerDay > 3) {
      findings.push({
        level: 'warning', title: 'Overtrading',
        detail: `${r.total_trades} trades over ${days} days (${tradesPerDay.toFixed(1)}/day). High frequency can rack up slippage and fees.`,
        recommendation: 'Strategy is firing too often. Raise confidence_threshold or increase the momentum signal threshold to be more selective. Quality over quantity.',
        param: 'confidence_threshold / signal threshold',
      });
    } else if (tradesPerDay < 0.05 && days > 30) {
      findings.push({
        level: 'warning', title: 'Very low trade frequency',
        detail: `${r.total_trades} trades over ${days} days (${tradesPerDay.toFixed(2)}/day). Rarely trades.`,
        recommendation: 'Strategy is too conservative. Lower confidence_threshold or add more symbols to the watchlist to increase opportunities.',
        param: 'confidence_threshold / watchlist',
      });
    } else if (r.total_trades >= 5) {
      findings.push({
        level: 'good', title: 'Healthy trade frequency',
        detail: `${r.total_trades} trades over ${days} days (${tradesPerDay.toFixed(1)}/day).`,
        recommendation: 'Trade frequency is reasonable — enough samples to be statistically meaningful but not overtrading.',
      });
    }

    // --- HOLD TIME ---
    if (r.total_trades >= 10) {
      if (r.avg_hold_days < 0.5) {
        findings.push({
          level: 'warning', title: 'Very short hold time',
          detail: `Average hold ${r.avg_hold_days.toFixed(1)} days — essentially day-trading.`,
          recommendation: 'TP/SL are very tight. Consider widening both to capture larger moves and reduce whipsaw losses from noise.',
          param: 'PROFIT_TARGET_PCT / STOP_LOSS_PCT',
        });
      } else if (r.avg_hold_days > 10) {
        findings.push({
          level: 'warning', title: 'Long hold time for a scalper',
          detail: `Average hold ${r.avg_hold_days.toFixed(1)} days — holding longer than expected for this strategy type.`,
          recommendation: 'TP may be too wide, or the strategy is holding through drawdowns. Check if STOP_LOSS_PCT is too loose.',
          param: 'PROFIT_TARGET_PCT / STOP_LOSS_PCT',
        });
      } else {
        findings.push({
          level: 'good', title: 'Reasonable hold time',
          detail: `Average hold ${r.avg_hold_days.toFixed(1)} days.`,
          recommendation: 'Hold period aligns with the strategy\'s intended time horizon.',
        });
      }
    }

    // --- PER-SYMBOL ANALYSIS ---
    if (bestSymbol && bestSymbol.total_pnl > 0 && r.total_trades >= 10) {
      findings.push({
        level: 'good', title: `Best performer: ${bestSymbol.symbol}`,
        detail: `${bestSymbol.symbol} generated $${bestSymbol.total_pnl.toFixed(0)} P&L with ${(bestSymbol.win_rate * 100).toFixed(0)}% win rate across ${bestSymbol.trades} trades.`,
        recommendation: 'This symbol suits the strategy well. Consider weighting it higher or adding correlated symbols to the watchlist.',
      });
    }
    if (worstSymbol && worstSymbol.total_pnl < -100 && r.total_trades >= 10) {
      findings.push({
        level: 'critical', title: `Worst performer: ${worstSymbol.symbol}`,
        detail: `${worstSymbol.symbol} lost $${Math.abs(worstSymbol.total_pnl).toFixed(0)} with ${(worstSymbol.win_rate * 100).toFixed(0)}% win rate across ${worstSymbol.trades} trades.`,
        recommendation: `This symbol is dragging down performance. Consider removing ${worstSymbol.symbol} from the watchlist or investigating why the strategy fails on it.`,
        param: 'watchlist',
      });
    }

    // --- WIN/LOSS SIZE ASYMMETRY ---
    if (r.total_trades >= 10 && avgWinPnl > 0 && avgLossPnl > 0) {
      const ratio = avgWinPnl / avgLossPnl;
      if (ratio < 0.8) {
        findings.push({
          level: 'critical', title: 'Losers bigger than winners',
          detail: `Average win $${avgWinPnl.toFixed(0)} vs average loss $${avgLossPnl.toFixed(0)} (ratio ${ratio.toFixed(2)}).`,
          recommendation: 'Risk/reward is inverted. Tighten STOP_LOSS_PCT to limit downside, and widen PROFIT_TARGET_PCT to capture more upside. This is the #1 thing to fix.',
          param: 'STOP_LOSS_PCT / PROFIT_TARGET_PCT',
        });
      } else if (ratio > 2.0) {
        findings.push({
          level: 'good', title: 'Winners much bigger than losers',
          detail: `Average win $${avgWinPnl.toFixed(0)} vs average loss $${avgLossPnl.toFixed(0)} (ratio ${ratio.toFixed(2)}).`,
          recommendation: 'Excellent risk/reward. Even with a modest win rate, this asymmetry drives profitability. Maintain current TP/SL levels.',
        });
      }
    }

    return findings;
  };

  const diagnosis = report ? generateDiagnosis(report) : [];

  const handleFetchLlmDiagnosis = async () => {
    if (!report) return;
    setLlmLoading(true);
    try {
      const resp = await fetch('/api/backtest/diagnose', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(report),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setLlmAvailable(data.available);
      if (data.diagnosis) {
        setLlmDiagnosis(data.diagnosis);
      }
    } catch {
      setLlmAvailable(false);
    } finally {
      setLlmLoading(false);
    }
  };

  const handleCopyReport = () => {
    if (!report || !selectedStrategy) return;
    const lines: string[] = [];
    lines.push('========================================');
    lines.push('  BACKTEST REPORT');
    lines.push('========================================');
    lines.push('');
    lines.push(`Strategy: ${report.agent_name} (${selectedKey})`);
    lines.push(`Tagline: ${selectedStrategy.tagline}`);
    lines.push(`Type: ${selectedStrategy.strategy_type}`);
    lines.push(`Risk Tolerance: ${selectedStrategy.risk_tolerance}`);
    lines.push(`Hold Period: ${selectedStrategy.hold_period}`);
    lines.push(`Watchlist: ${selectedStrategy.watchlist.join(', ')}`);
    lines.push('');
    lines.push('--- Test Parameters ---');
    lines.push(`Start Date: ${report.start_date}`);
    lines.push(`End Date: ${report.end_date}`);
    lines.push(`Initial Capital: $${report.initial_capital.toLocaleString()}`);
    lines.push(`Symbols Tested: ${report.symbols.join(', ')}`);
    lines.push(`Candle Interval: ${report.interval || '1d'}`);
    lines.push(`Slippage: ${report.slippage_bps ?? 0} bps`);
    if (symbolsInput.trim()) {
      lines.push(`Symbols Override: ${symbolsInput}`);
    }
    lines.push('');
    lines.push('--- Results Summary ---');
    lines.push(`Total Return: ${fmtPct(report.total_return_pct, true)}`);
    lines.push(`Net P&L: ${report.final_equity - report.initial_capital >= 0 ? '+' : ''}${fmtCurrency(report.final_equity - report.initial_capital)}`);
    lines.push(`Final Equity: ${fmtCurrency(report.final_equity)}`);
    lines.push(`Sharpe Ratio: ${report.sharpe_ratio.toFixed(3)}`);
    lines.push(`Max Drawdown: ${fmtPct(report.max_drawdown_pct)}`);
    lines.push(`Win Rate: ${(report.win_rate * 100).toFixed(1)}% (${report.winning_trades}W / ${report.losing_trades}L)`);
    lines.push(`Profit Factor: ${report.profit_factor.toFixed(3)}`);
    lines.push(`Total Trades: ${report.total_trades}`);
    lines.push(`Avg Hold Days: ${report.avg_hold_days.toFixed(1)}`);
    if (report.diagnostics) {
      lines.push('');
      lines.push('--- Execution Diagnostics ---');
      lines.push(`Scans: ${report.diagnostics.scan_bars ?? 0}`);
      lines.push(`Entry rejected: ${report.diagnostics.entry_rejected ?? 0}`);
      lines.push(`Trend rejected: ${report.diagnostics.trend_rejected ?? 0}`);
      lines.push(`Qualified setups: ${report.diagnostics.setup_qualified ?? 0}`);
      lines.push(`Orders: ${report.diagnostics.orders_placed ?? 0} placed / ${report.diagnostics.orders_filled ?? 0} filled`);
      lines.push(`Expired pending orders: ${report.diagnostics.orders_expired ?? 0}`);
      lines.push(`Same-bar exits skipped: ${report.diagnostics.same_bar_exit_skipped ?? 0}`);
    }
    lines.push('');
    lines.push('--- Per-Symbol Breakdown ---');
    const symStats = Object.entries(report.per_symbol_stats).sort((a, b) => b[1].total_pnl - a[1].total_pnl);
    for (const [sym, s] of symStats) {
      lines.push(`  ${sym}: ${s.trades} trades, ${(s.win_rate * 100).toFixed(0)}% win, P&L $${s.total_pnl.toFixed(2)}, avg ${s.avg_pnl_pct.toFixed(2)}%`);
    }
    lines.push('');
    if (diagnosis.length > 0) {
      lines.push('--- Strategy Diagnosis ---');
      for (const d of diagnosis) {
        const tag = d.level === 'good' ? '[OK]' : d.level === 'warning' ? '[WARN]' : '[CRIT]';
        lines.push(`  ${tag} ${d.title}`);
        lines.push(`       ${d.detail}`);
        lines.push(`       → ${d.recommendation}${d.param ? ` (${d.param})` : ''}`);
      }
      lines.push('');
    }
    if (report.trades.length > 0) {
      lines.push('--- Trade Log ---');
      lines.push('  Symbol  Side  Entry Date  Exit Date   Entry Price  Exit Price  Qty       P&L         P&L%    Hold  Reason');
      for (const t of report.trades) {
        lines.push(
          `  ${t.symbol.padEnd(7)} ${t.side.padEnd(5)} ${t.entry_date.padEnd(11)} ${t.exit_date.padEnd(11)} ` +
          `${t.entry_price.toFixed(2).padStart(11)} ${t.exit_price.toFixed(2).padStart(10)} ` +
          `${t.quantity.toFixed(6).padStart(9)} ${t.pnl.toFixed(2).padStart(10)} ${t.pnl_pct.toFixed(2).padStart(6)}% ` +
          `${t.hold_days.toFixed(1).padStart(4)}d  ${t.reason.substring(0, 60)}`
        );
      }
    }
    lines.push('');
    lines.push(`Generated: ${new Date().toISOString()}`);
    lines.push('========================================');
    const text = lines.join('\n');
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="flex items-center gap-2 mb-1">
        <FlaskConical size={18} className="text-arena-purple" />
        <h1 className="text-lg font-bold text-white">Strategy Backtesting</h1>
      </div>
      <p className="text-xs text-arena-text-dim mb-6">
        Replay historical data through any agent's strategy and visualize performance
      </p>

      {/* Config Panel */}
      <div className="card-base p-4 mb-6">
        {/* Test Scenarios */}
        <div className="mb-4">
          <div className="flex items-center gap-2 mb-2">
            <Rocket size={11} className="text-arena-yellow" />
            <span className="text-[10px] text-arena-text-dim uppercase tracking-wider">Test Scenarios</span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {testPresets.map(p => (
              <button
                key={p.id}
                onClick={() => applyTestPreset(p)}
                className={`text-left p-3 rounded-lg transition-all border ${
                  activeTestPreset === p.id
                    ? 'bg-arena-yellow/10 border-arena-yellow/40 ring-1 ring-arena-yellow/20'
                    : 'bg-white/5 border-white/6 hover:bg-white/8 hover:border-white/10'
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className={`text-[11px] font-bold ${
                    activeTestPreset === p.id ? 'text-arena-yellow' : 'text-white'
                  }`}>
                    {p.label}
                  </span>
                  <span className="text-[9px] text-arena-text-dim font-mono">
                    {p.interval} · {Number(p.capital) >= 1000 ? `$${Math.round(Number(p.capital) / 1000)}K` : `$${p.capital}`}
                  </span>
                </div>
                <p className="text-[10px] text-arena-text-dim leading-relaxed">
                  {p.description}
                </p>
                <div className="flex items-center gap-2 mt-1.5 text-[9px] text-arena-text-dim/70">
                  <span>{p.startDate} → {p.endDate}</span>
                  {p.symbols && <span className="px-1 py-0.5 rounded bg-white/5 font-mono">{p.symbols}</span>}
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Quick Date Presets */}
        <div className="flex items-center gap-2 mb-4 flex-wrap">
          <span className="text-[10px] text-arena-text-dim uppercase tracking-wider flex items-center gap-1">
            <Calendar size={11} /> Quick Range
          </span>
          {['1M', '3M', '6M', '1Y', '2Y', 'YTD'].map(p => (
            <button
              key={p}
              onClick={() => applyPreset(p)}
              className={`px-2.5 py-1 rounded-md text-[10px] font-semibold transition-colors border ${
                activePreset === p
                  ? 'bg-arena-purple/20 border-arena-purple/40 text-arena-purple'
                  : 'bg-white/5 border-white/6 text-arena-text-secondary hover:bg-white/10 hover:text-white'
              }`}
            >
              {p}
            </button>
          ))}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {/* Strategy Selector */}
          <div>
            <label className="text-[10px] text-arena-text-dim mb-1 block uppercase tracking-wider">Strategy</label>
            {strategiesLoading ? (
              <div className="form-input flex items-center gap-2">
                <Loader2 size={12} className="animate-spin" />
                <span className="text-arena-text-dim">Loading...</span>
              </div>
            ) : (
              <select
                className="form-input"
                value={selectedKey}
                onChange={e => {
                  setSelectedKey(e.target.value);
                  setParamsOverride(null);
                  setSignalAnalysis(null);
                  setActiveTestPreset('');
                }}
              >
                {strategies.map(s => (
                  <option key={s.key} value={s.key}>{s.name}</option>
                ))}
              </select>
            )}
            {selectedStrategy && (
              <div className="mt-1.5 text-[10px] text-arena-text-dim">
                {selectedStrategy.tagline}
              </div>
            )}
          </div>

          {/* Start Date */}
          <div>
            <label className="text-[10px] text-arena-text-dim mb-1 block uppercase tracking-wider">Start Date</label>
            <input
              type="date"
              className="form-input date-picker-dark"
              value={startDate}
              onChange={e => { setStartDate(e.target.value); setActivePreset(''); }}
            />
          </div>

          {/* End Date */}
          <div>
            <label className="text-[10px] text-arena-text-dim mb-1 block uppercase tracking-wider">End Date</label>
            <input
              type="date"
              className="form-input date-picker-dark"
              value={endDate}
              onChange={e => { setEndDate(e.target.value); setActivePreset(''); }}
            />
          </div>

          {/* Initial Capital */}
          <div>
            <label className="text-[10px] text-arena-text-dim mb-1 block uppercase tracking-wider">Initial Capital</label>
            <input
              type="number"
              className="form-input"
              value={capital}
              onChange={e => setCapital(e.target.value)}
              min="1000"
              step="1000"
            />
          </div>

          {/* Candle Interval */}
          <div>
            <label className="text-[10px] text-arena-text-dim mb-1 block uppercase tracking-wider">Candle Interval</label>
            <select
              className="form-input"
              value={candleInterval}
              onChange={e => setCandleInterval(e.target.value)}
            >
              <option value="1d">Daily (1d) — swing/position strategies</option>
              <option value="1h">1 Hour (1h) — intraday, up to ~2yr history</option>
              <option value="15m">15 Min (15m) — scalp/momentum, ~60d history</option>
              <option value="5m">5 Min (5m) — high-frequency scalp, ~60d history</option>
            </select>
            {candleInterval !== '1d' && (
              <div className="mt-1 text-[9px] text-arena-yellow/80">
                Intraday bars have limited history (~60 days for 5m/15m). Date range will be clamped automatically.
              </div>
            )}
          </div>

          {/* Slippage */}
          <div>
            <label className="text-[10px] text-arena-text-dim mb-1 block uppercase tracking-wider">Slippage (bps)</label>
            <input
              type="number"
              className="form-input"
              value={slippageBps}
              onChange={e => setSlippageBps(e.target.value)}
              min="0"
              step="1"
            />
            <div className="mt-1 text-[9px] text-arena-text-dim">5 bps = 0.05% adverse fill per trade. Set 0 for idealized fills.</div>
          </div>

          {/* Goal Target */}
          <div>
            <label className="text-[10px] text-arena-text-dim mb-1 block uppercase tracking-wider">Goal Target ($)</label>
            <input
              type="number"
              className="form-input"
              value={goalTarget}
              onChange={e => setGoalTarget(e.target.value)}
              min="0"
              step="100"
              placeholder="Default: 10% of capital"
            />
            <div className="mt-1 text-[9px] text-arena-text-dim">Dollar profit target for goal-aware position sizing. Leave empty for default.</div>
          </div>
        </div>

        {/* Symbols Override */}
        <div className="mt-4">
          <label className="text-[10px] text-arena-text-dim mb-1 block uppercase tracking-wider">
            Symbols Override <span className="text-arena-text-dim normal-case">(comma-separated, leave empty for agent watchlist)</span>
          </label>
          <input
            type="text"
            className="form-input"
            value={symbolsInput}
            onChange={e => setSymbolsInput(e.target.value)}
            placeholder={selectedStrategy ? `Default: ${selectedStrategy.watchlist.join(', ')}` : 'e.g. BTC,ETH,SOL'}
          />
        </div>

        {/* Run Button */}
        <div className="mt-4 flex items-center gap-3">
          <button
            onClick={handleRun}
            disabled={running || !selectedKey}
            className="flex items-center gap-2 px-4 py-2 bg-arena-purple/20 border border-arena-purple/40 rounded-lg text-arena-purple text-xs font-semibold hover:bg-arena-purple/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {running ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
            {running ? 'Running...' : 'Run Backtest'}
          </button>
          {selectedKey === 'scalprunner' && (
            <button
              onClick={handleAnalyzeSignals}
              disabled={analysisRunning || running || !startDate || !endDate}
              className="flex items-center gap-2 px-4 py-2 bg-arena-yellow/10 border border-arena-yellow/30 rounded-lg text-arena-yellow text-xs font-semibold hover:bg-arena-yellow/20 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {analysisRunning ? <Loader2 size={14} className="animate-spin" /> : <Stethoscope size={14} />}
              {analysisRunning ? 'Analyzing...' : 'Analyze Signals'}
            </button>
          )}
          {error && (
            <span className="text-xs text-arena-red">{error}</span>
          )}
        </div>
      </div>

      {signalAnalysis && (
        <div className="card-base p-4 mb-6">
          <div className="flex items-center justify-between mb-3">
            <div>
              <h2 className="text-xs font-bold text-white flex items-center gap-1.5">
                <Stethoscope size={13} className="text-arena-yellow" />
                Entry Outcome Analysis
              </h2>
              <div className="text-[10px] text-arena-text-dim mt-1">
                Forward MFE/MAE on qualifying signals; no portfolio sizing or trade simulation.
              </div>
            </div>
            <div className="text-[10px] text-arena-text-dim">
              {signalAnalysis.signal_count} signals · {signalAnalysis.triggered_count} triggered
            </div>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-[10px]">
            <MetricCard label="Fill Rate" value={`${((signalAnalysis.fill_rate || 0) * 100).toFixed(1)}%`} />
            <MetricCard label="Resolved" value={String(signalAnalysis.resolved_count || 0)} />
            <MetricCard label="Resolved Win Rate" value={`${((signalAnalysis.resolved_win_rate || 0) * 100).toFixed(1)}%`} />
            <MetricCard label="Expectancy (R)" value={(signalAnalysis.expectancy_r || 0).toFixed(3)} />
            <MetricCard label="Target First" value={String(signalAnalysis.outcomes?.target_first || 0)} />
          </div>
          <div className="mt-3 grid grid-cols-2 md:grid-cols-4 gap-2">
            {Object.entries(signalAnalysis.horizon_stats || {}).map(([horizon, stats]: [string, any]) => (
              <div key={horizon} className="rounded-lg border border-arena-border/60 bg-white/[0.02] p-2 text-[10px] text-arena-text-dim">
                <div className="font-semibold text-arena-text-secondary">{horizon} bars forward</div>
                <div>MFE {(stats.avg_mfe_pct || 0).toFixed(2)}%</div>
                <div>MAE {(stats.avg_mae_pct || 0).toFixed(2)}%</div>
                <div>Close {(stats.avg_close_return_pct || 0).toFixed(2)}%</div>
              </div>
            ))}
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            {Object.entries(signalAnalysis.per_symbol || {}).map(([symbol, stats]: [string, any]) => (
              <div key={symbol} className="rounded-lg border border-arena-border/60 bg-white/[0.02] px-2 py-1.5 text-[10px] text-arena-text-dim">
                <span className="font-semibold text-arena-text-secondary">{symbol}</span>
                {' · '}{stats.signals} signals
                {' · '}{((stats.win_rate || 0) * 100).toFixed(0)}% resolved win
                {' · '}{stats.outcomes?.target_first || 0}T / {stats.outcomes?.stop_first || 0}S
              </div>
            ))}
          </div>
          {signalAnalysis.sample_warning && (
            <div className="mt-3 text-[10px] text-arena-yellow">{signalAnalysis.sample_warning}</div>
          )}
        </div>
      )}

      {/* Results */}
      {report && (
        <div className="space-y-6">
          {/* P&L Hero */}
          <div className="card-base p-6">
            <div className="flex items-center justify-between mb-4">
              <div>
                <div className="text-sm font-bold text-white">{report.agent_name}</div>
                <div className="text-[10px] text-arena-text-dim">
                  {report.start_date} → {report.end_date} | {report.total_trades} trades | {report.interval || '1d'}{report.slippage_bps ? ` | ${report.slippage_bps}bps slip` : ''}
                </div>
              </div>
              <button
                onClick={handleCopyReport}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-white/5 border border-white/10 rounded-lg text-[10px] font-semibold text-arena-text-secondary hover:bg-white/10 hover:text-white transition-colors"
              >
                {copied ? <Check size={12} className="text-arena-green" /> : <Copy size={12} />}
                {copied ? 'Copied!' : 'Copy Report'}
              </button>
            </div>

            {/* Big P&L Numbers */}
            <div className="flex items-end gap-8 mb-6">
              {/* Total Return % */}
              <div>
                <div className="text-[10px] text-arena-text-dim uppercase tracking-wider mb-1">Total Return</div>
                <div className={`flex items-center gap-2 text-4xl font-bold ${report.total_return_pct >= 0 ? 'text-arena-green' : 'text-arena-red'}`}>
                  {report.total_return_pct >= 0 ? <TrendingUp size={32} /> : <TrendingDown size={32} />}
                  {fmtPct(report.total_return_pct, true)}
                </div>
              </div>

              {/* Dollar P&L */}
              <div>
                <div className="text-[10px] text-arena-text-dim uppercase tracking-wider mb-1">Net P&L</div>
                <div className={`text-3xl font-bold font-mono ${report.total_return_pct >= 0 ? 'text-arena-green' : 'text-arena-red'}`}>
                  {report.final_equity - report.initial_capital >= 0 ? '+' : ''}{fmtCurrency(report.final_equity - report.initial_capital)}
                </div>
              </div>

              {/* Final Equity */}
              <div>
                <div className="text-[10px] text-arena-text-dim uppercase tracking-wider mb-1">Final Equity</div>
                <div className="text-3xl font-bold font-mono text-white">
                  {fmtCurrency(report.final_equity)}
                </div>
              </div>
            </div>

            {/* Metric Cards */}
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
              <MetricCard label="Sharpe Ratio" value={report.sharpe_ratio.toFixed(3)} />
              <MetricCard label="Max Drawdown" value={fmtPct(report.max_drawdown_pct)} valueClass="text-arena-red" />
              <MetricCard label="Win Rate" value={`${(report.win_rate * 100).toFixed(1)}%`} subValue={`${report.winning_trades}W / ${report.losing_trades}L`} />
              <MetricCard label="Profit Factor" value={report.profit_factor.toFixed(3)} />
              <MetricCard label="Avg Hold" value={`${(report.avg_hold_hours ?? report.avg_hold_days * 24).toFixed(1)}h`} />
              <MetricCard label="Total Trades" value={String(report.total_trades)} />
            </div>
            {report.activation_gate && (
              <div className={`mt-4 rounded-lg border px-3 py-2 text-[10px] ${report.activation_gate.eligible ? 'border-arena-green/30 bg-arena-green/5 text-arena-green' : 'border-arena-yellow/30 bg-arena-yellow/5 text-arena-yellow'}`}>
                <strong>{report.activation_gate.eligible ? 'Activation eligible' : 'Paper-only: quantitative gate not passed'}</strong>
                {' · '}{Object.entries(report.activation_gate.checks).filter(([, passed]) => !passed).map(([key]) => key.split('_').join(' ')).join(', ') || 'all checks passed'}
              </div>
            )}
            {report.diagnostics && (
              <div className="mt-4 rounded-lg border border-arena-border/60 bg-white/[0.02] px-3 py-2 text-[10px] text-arena-text-dim">
                <div className="font-semibold text-arena-text-secondary mb-1">Execution Diagnostics</div>
                <div className="flex flex-wrap gap-x-4 gap-y-1">
                  <span>Scans: {report.diagnostics.scan_bars ?? 0}</span>
                  <span>Entry rejected: {report.diagnostics.entry_rejected ?? 0}</span>
                  <span>Trend rejected: {report.diagnostics.trend_rejected ?? 0}</span>
                  <span>Qualified: {report.diagnostics.setup_qualified ?? 0}</span>
                  <span>Orders: {report.diagnostics.orders_placed ?? 0} placed / {report.diagnostics.orders_filled ?? 0} filled</span>
                  <span>Expired: {report.diagnostics.orders_expired ?? 0}</span>
                  <span>Same-bar exits skipped: {report.diagnostics.same_bar_exit_skipped ?? 0}</span>
                </div>
                {report.diagnostics.sample_warning && (
                  <div className="mt-1 text-arena-yellow">{report.diagnostics.sample_warning}</div>
                )}
              </div>
            )}
          </div>

          {/* Strategy Diagnosis */}
          {diagnosis.length > 0 && (
            <div className="card-base p-4">
              <h2 className="text-xs font-bold text-white mb-3 flex items-center gap-1.5">
                <Stethoscope size={12} className="text-arena-purple" />
                Strategy Diagnosis
              </h2>
              <div className="space-y-2">
                {diagnosis.map((item, i) => {
                  const icon = item.level === 'good'
                    ? <CheckCircle size={14} className="text-arena-green shrink-0 mt-0.5" />
                    : item.level === 'warning'
                    ? <AlertTriangle size={14} className="text-arena-yellow shrink-0 mt-0.5" />
                    : <XCircle size={14} className="text-arena-red shrink-0 mt-0.5" />;
                  const bgClass = item.level === 'good'
                    ? 'border-arena-green/20 bg-arena-green/5'
                    : item.level === 'warning'
                    ? 'border-arena-yellow/20 bg-arena-yellow/5'
                    : 'border-arena-red/20 bg-arena-red/5';
                  return (
                    <div key={i} className={`rounded-lg border p-3 ${bgClass}`}>
                      <div className="flex items-start gap-2">
                        {icon}
                        <div className="flex-1 min-w-0">
                          <div className="text-xs font-bold text-white">{item.title}</div>
                          <div className="text-[11px] text-arena-text-dim mt-0.5">{item.detail}</div>
                          <div className="flex items-start gap-1 mt-1.5">
                            <Lightbulb size={11} className="text-arena-purple shrink-0 mt-0.5" />
                            <div className="text-[11px] text-arena-text-secondary">
                              {item.recommendation}
                              {item.param && (
                                <span className="text-arena-purple font-mono ml-1">→ {item.param}</span>
                              )}
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* LLM-Powered Analysis */}
          <div className="card-base p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-xs font-bold text-white flex items-center gap-1.5">
                <Sparkles size={12} className="text-arena-purple" />
                AI Analysis
              </h2>
              {report && (
                <button
                  onClick={handleFetchLlmDiagnosis}
                  disabled={llmLoading}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-arena-purple/20 border border-arena-purple/40 rounded-lg text-[10px] font-semibold text-arena-purple hover:bg-arena-purple/30 transition-colors disabled:opacity-50"
                >
                  {llmLoading ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
                  {llmLoading ? 'Analyzing...' : 'Get AI Analysis'}
                </button>
              )}
            </div>
            {llmAvailable === false && !llmLoading && (
              <div className="text-[11px] text-arena-text-dim py-2">
                LLM not available. Start Ollama (<code className="text-arena-purple">ollama serve</code>) to enable AI-powered analysis. The templated diagnosis above is still available.
              </div>
            )}
            {llmAvailable === true && llmDiagnosis && (
              <div className="bg-arena-bg rounded-lg p-3 border border-arena-border/50">
                <pre className="text-[11px] text-arena-text-secondary whitespace-pre-wrap font-mono leading-relaxed">{llmDiagnosis}</pre>
              </div>
            )}
            {llmAvailable === null && !llmLoading && report && (
              <div className="text-[11px] text-arena-text-dim py-2">
                Click <span className="text-arena-purple">Get AI Analysis</span> to send the report to the LLM for a deeper, context-aware diagnosis. Falls back to the templated diagnosis above if unavailable.
              </div>
            )}
          </div>

          {/* Equity Curve */}
          {equityData.length > 0 && (
            <div className="card-base p-4">
              <h2 className="text-xs font-bold text-white mb-3 flex items-center gap-1.5">
                <Zap size={12} className="text-arena-purple" />
                Equity Curve
              </h2>
              <ResponsiveContainer width="100%" height={280}>
                <AreaChart data={equityData}>
                  <defs>
                    <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#8B5CF6" stopOpacity={0.3} />
                      <stop offset="100%" stopColor="#8B5CF6" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                  <XAxis
                    dataKey="date"
                    tick={{ fill: '#5A6275', fontSize: 10 }}
                    tickLine={false}
                    axisLine={false}
                    minTickGap={40}
                  />
                  <YAxis
                    tick={{ fill: '#5A6275', fontSize: 10 }}
                    tickLine={false}
                    axisLine={false}
                    tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#10141B',
                      border: '1px solid rgba(255,255,255,0.08)',
                      borderRadius: '8px',
                      fontSize: '11px',
                    }}
                    labelStyle={{ color: '#8B92A5' }}
                    formatter={(v: number) => [fmtCurrency(v), 'Equity']}
                  />
                  <Area
                    type="monotone"
                    dataKey="equity"
                    stroke="#8B5CF6"
                    strokeWidth={2}
                    fill="url(#equityGrad)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Per-Symbol Stats */}
          {symbolStatsData.length > 0 && (
            <div className="card-base p-4">
              <h2 className="text-xs font-bold text-white mb-3">Per-Symbol Breakdown</h2>
              <div className="overflow-x-auto">
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="text-arena-text-dim border-b border-arena-border">
                      <th className="text-left py-2 px-2 font-medium">Symbol</th>
                      <th className="text-right py-2 px-2 font-medium">Trades</th>
                      <th className="text-right py-2 px-2 font-medium">Wins</th>
                      <th className="text-right py-2 px-2 font-medium">Win Rate</th>
                      <th className="text-right py-2 px-2 font-medium">Total PnL</th>
                      <th className="text-right py-2 px-2 font-medium">Avg PnL %</th>
                    </tr>
                  </thead>
                  <tbody>
                    {symbolStatsData.map(s => (
                      <tr key={s.symbol} className="border-b border-arena-border/50 hover:bg-white/2">
                        <td className="py-2 px-2 text-white font-medium">{s.symbol}</td>
                        <td className="py-2 px-2 text-right text-arena-text-secondary">{s.trades}</td>
                        <td className="py-2 px-2 text-right text-arena-green">{s.wins}</td>
                        <td className="py-2 px-2 text-right text-arena-text-secondary">{(s.win_rate * 100).toFixed(1)}%</td>
                        <td className={`py-2 px-2 text-right font-mono ${s.total_pnl >= 0 ? 'text-arena-green' : 'text-arena-red'}`}>
                          {s.total_pnl >= 0 ? '+' : ''}{fmtCurrency(s.total_pnl)}
                        </td>
                        <td className={`py-2 px-2 text-right font-mono ${s.avg_pnl_pct >= 0 ? 'text-arena-green' : 'text-arena-red'}`}>
                          {s.avg_pnl_pct >= 0 ? '+' : ''}{s.avg_pnl_pct.toFixed(2)}%
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* PnL Bar Chart by Symbol */}
          {symbolStatsData.length > 0 && (
            <div className="card-base p-4">
              <h2 className="text-xs font-bold text-white mb-3">PnL by Symbol</h2>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={symbolStatsData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                  <XAxis
                    dataKey="symbol"
                    tick={{ fill: '#5A6275', fontSize: 10 }}
                    tickLine={false}
                    axisLine={false}
                  />
                  <YAxis
                    tick={{ fill: '#5A6275', fontSize: 10 }}
                    tickLine={false}
                    axisLine={false}
                    tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#10141B',
                      border: '1px solid rgba(255,255,255,0.08)',
                      borderRadius: '8px',
                      fontSize: '11px',
                    }}
                    formatter={(v: number) => [fmtCurrency(v), 'PnL']}
                  />
                  <Bar dataKey="total_pnl" radius={[4, 4, 0, 0]}>
                    {symbolStatsData.map((entry, i) => (
                      <Cell key={i} fill={entry.total_pnl >= 0 ? '#10B981' : '#EF4444'} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Trade Log */}
          {report.trades.length > 0 && (
            <div className="card-base p-4">
              <h2 className="text-xs font-bold text-white mb-3">
                Trade Log <span className="text-arena-text-dim font-normal">({report.trades.length} trades)</span>
              </h2>
              <div className="overflow-x-auto max-h-96 overflow-y-auto">
                <table className="w-full text-[10px]">
                  <thead className="sticky top-0 bg-arena-card z-10">
                    <tr className="text-arena-text-dim border-b border-arena-border">
                      <th className="text-left py-2 px-2 font-medium">Symbol</th>
                      <th className="text-left py-2 px-2 font-medium">Side</th>
                      <th className="text-left py-2 px-2 font-medium">Entry</th>
                      <th className="text-left py-2 px-2 font-medium">Exit</th>
                      <th className="text-right py-2 px-2 font-medium">Entry $</th>
                      <th className="text-right py-2 px-2 font-medium">Exit $</th>
                      <th className="text-right py-2 px-2 font-medium">Qty</th>
                      <th className="text-right py-2 px-2 font-medium">PnL</th>
                      <th className="text-right py-2 px-2 font-medium">PnL %</th>
                      <th className="text-right py-2 px-2 font-medium">Hold</th>
                      <th className="text-left py-2 px-2 font-medium">Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.trades.map((t, i) => (
                      <tr key={i} className="border-b border-arena-border/30 hover:bg-white/2">
                        <td className="py-1.5 px-2 text-white font-medium">{t.symbol}</td>
                        <td className="py-1.5 px-2">
                          <span className={t.side === 'long' ? 'text-arena-green' : 'text-arena-red'}>
                            {t.side.toUpperCase()}
                          </span>
                        </td>
                        <td className="py-1.5 px-2 text-arena-text-secondary">{t.entry_date}</td>
                        <td className="py-1.5 px-2 text-arena-text-secondary">{t.exit_date}</td>
                        <td className="py-1.5 px-2 text-right font-mono text-arena-text-secondary">${t.entry_price.toFixed(2)}</td>
                        <td className="py-1.5 px-2 text-right font-mono text-arena-text-secondary">${t.exit_price.toFixed(2)}</td>
                        <td className="py-1.5 px-2 text-right font-mono text-arena-text-secondary">{t.quantity.toFixed(4)}</td>
                        <td className={`py-1.5 px-2 text-right font-mono font-medium ${t.pnl >= 0 ? 'text-arena-green' : 'text-arena-red'}`}>
                          {t.pnl >= 0 ? '+' : ''}{fmtCurrency(t.pnl)}
                        </td>
                        <td className={`py-1.5 px-2 text-right font-mono ${t.pnl_pct >= 0 ? 'text-arena-green' : 'text-arena-red'}`}>
                          {t.pnl_pct >= 0 ? '+' : ''}{t.pnl_pct.toFixed(2)}%
                        </td>
                        <td className="py-1.5 px-2 text-right text-arena-text-secondary">{t.hold_days}d</td>
                        <td className="py-1.5 px-2 text-arena-text-dim max-w-xs truncate" title={t.reason}>{t.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Empty State */}
      {!report && !running && !error && (
        <div className="card-base p-12 flex flex-col items-center justify-center text-center">
          <FlaskConical size={32} className="text-arena-text-dim mb-3" />
          <div className="text-sm text-arena-text-dim mb-1">No backtest results yet</div>
          <div className="text-[10px] text-arena-text-dim">
            Select a strategy and click <span className="text-arena-purple">Run Backtest</span> to see performance metrics
          </div>
        </div>
      )}
    </div>
  );
}

function MetricCard({
  label,
  value,
  subValue,
  valueClass,
}: {
  label: string;
  value: string;
  subValue?: string;
  valueClass?: string;
}) {
  return (
    <div className="bg-arena-bg rounded-lg p-3 border border-arena-border/50">
      <div className="text-[9px] text-arena-text-dim uppercase tracking-wider mb-1">{label}</div>
      <div className={`text-sm font-bold font-mono ${valueClass || 'text-white'}`}>{value}</div>
      {subValue && <div className="text-[9px] text-arena-text-dim mt-0.5">{subValue}</div>}
    </div>
  );
}
