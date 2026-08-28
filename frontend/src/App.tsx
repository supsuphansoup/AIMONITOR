import React, { Suspense, lazy, useCallback, useEffect, useRef, useState } from 'react';
import { AlertsModal } from './components/AlertsModal';
import { ConnectionBanner } from './components/ConnectionBanner';
import { Header } from './components/Header';
import { KPICards } from './components/KPICards';
import { SettingsModal } from './components/SettingsModal';
import { TraceDetailModal } from './components/TraceDetailModal';
import { TraceTable } from './components/TraceTable';
import { api, ApiError } from './api';
import {
  AlertHistory,
  AlertRule,
  ModelBreakdown,
  OverviewMetrics,
  SystemInfo,
  TIME_WINDOWS,
  TimeseriesPoint,
  TraceEvent,
} from './types';

// Recharts is by far the heaviest dependency; deferring it keeps the initial
// bundle small and the KPI row painting immediately.
const Charts = lazy(() =>
  import('./components/Charts').then((m) => ({ default: m.Charts })),
);

const POLL_INTERVAL_MS = 5000;
const PAGE_SIZE = 50;

export const App: React.FC = () => {
  const [metrics, setMetrics] = useState<OverviewMetrics | null>(null);
  const [timeseries, setTimeseries] = useState<TimeseriesPoint[]>([]);
  const [breakdown, setBreakdown] = useState<ModelBreakdown[]>([]);
  const [traces, setTraces] = useState<TraceEvent[]>([]);
  const [traceTotal, setTraceTotal] = useState<number>(0);
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [histories, setHistories] = useState<AlertHistory[]>([]);
  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null);

  const [windowIndex, setWindowIndex] = useState<number>(2); // 24H
  const [page, setPage] = useState<number>(0);
  const [selectedProvider, setSelectedProvider] = useState<string>('');
  const [selectedStatus, setSelectedStatus] = useState<string>('');
  const [selectedTrace, setSelectedTrace] = useState<TraceEvent | null>(null);

  const [isAlertsOpen, setIsAlertsOpen] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [isSimulating, setIsSimulating] = useState(false);
  const [error, setError] = useState<{ message: string; status?: number } | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const inFlight = useRef<AbortController | null>(null);
  const timeWindow = TIME_WINDOWS[windowIndex];

  const loadData = useCallback(async () => {
    // Cancel a slow previous poll so a stalled backend cannot queue up requests.
    inFlight.current?.abort();
    const controller = new AbortController();
    inFlight.current = controller;

    setIsLoading(true);
    try {
      const [overview, series, models, traceRows, ruleRows, historyRows] = await Promise.all([
        api.overview(timeWindow.hours, controller.signal),
        api.timeseries(timeWindow.hours, timeWindow.intervalMinutes, controller.signal),
        api.breakdown(timeWindow.hours, controller.signal),
        api.traces(
          {
            provider: selectedProvider || undefined,
            status: selectedStatus || undefined,
            limit: PAGE_SIZE,
            offset: page * PAGE_SIZE,
          },
          controller.signal,
        ),
        api.alertRules(controller.signal),
        api.alertHistory(controller.signal),
      ]);

      setMetrics(overview);
      setTimeseries(series);
      setBreakdown(models);
      setTraces(traceRows.items);
      setTraceTotal(traceRows.total);
      setRules(ruleRows);
      setHistories(historyRows);
      setError(null);
      setLastUpdated(new Date());
    } catch (err) {
      if ((err as Error)?.name === 'AbortError') return;
      // Surfaced in the banner rather than swallowed: an unreachable backend
      // must not look like a quiet one.
      setError(
        err instanceof ApiError
          ? { message: err.message, status: err.status }
          : { message: '백엔드에 연결할 수 없습니다. 서버가 실행 중인지 확인하세요.' },
      );
    } finally {
      if (inFlight.current === controller) {
        inFlight.current = null;
        setIsLoading(false);
      }
    }
  }, [selectedProvider, selectedStatus, page, timeWindow.hours, timeWindow.intervalMinutes]);

  useEffect(() => {
    api.systemInfo().then(setSystemInfo).catch(() => setSystemInfo(null));
  }, []);

  useEffect(() => {
    loadData();
    let timer = window.setInterval(loadData, POLL_INTERVAL_MS);

    // Polling a hidden tab burns backend cycles for a dashboard nobody is reading.
    const onVisibilityChange = () => {
      window.clearInterval(timer);
      if (document.visibilityState === 'visible') {
        loadData();
        timer = window.setInterval(loadData, POLL_INTERVAL_MS);
      }
    };
    document.addEventListener('visibilitychange', onVisibilityChange);

    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisibilityChange);
      inFlight.current?.abort();
    };
  }, [loadData]);

  const handleSimulate = async () => {
    setIsSimulating(true);
    const sampleModels = [
      { provider: 'openai', model: 'gpt-4o', pTkn: 800, cTkn: 400, lat: 420 },
      { provider: 'openai', model: 'gpt-4o-mini', pTkn: 350, cTkn: 150, lat: 180 },
      { provider: 'anthropic', model: 'claude-3-5-sonnet', pTkn: 1200, cTkn: 600, lat: 650 },
      { provider: 'anthropic', model: 'claude-3-5-haiku', pTkn: 400, cTkn: 200, lat: 210 },
      { provider: 'gemini', model: 'gemini-1.5-pro', pTkn: 2500, cTkn: 800, lat: 510 },
      { provider: 'gemini', model: 'gemini-2.0-flash', pTkn: 500, cTkn: 300, lat: 140 },
      { provider: 'ollama', model: 'llama3.1', pTkn: 600, cTkn: 250, lat: 890 },
    ];

    const batch = Array.from({ length: 6 }).map((_, i) => {
      const item = sampleModels[Math.floor(Math.random() * sampleModels.length)];
      const isError = Math.random() < 0.15;
      const usesCache = item.provider === 'anthropic' && Math.random() < 0.5;
      return {
        provider: item.provider,
        model_name: item.model,
        prompt_tokens: item.pTkn + Math.floor(Math.random() * 200),
        completion_tokens: isError ? 0 : item.cTkn + Math.floor(Math.random() * 100),
        cache_read_tokens: usesCache ? 4000 + Math.floor(Math.random() * 4000) : 0,
        cache_write_tokens: usesCache ? 1000 : 0,
        latency_ms: item.lat + Math.floor(Math.random() * 150),
        status: isError ? 'ERROR' : 'SUCCESS',
        error_message: isError ? 'HTTP 429: Rate limit exceeded or upstream timeout' : undefined,
        tags: ['demo-feed', `batch-${i}`],
        metadata: { client: 'simulation-engine', env: 'production' },
      };
    });

    try {
      await api.ingestBatch(batch);
      await api.evaluateRules();
      await loadData();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? { message: err.message, status: err.status }
          : { message: '데모 트래픽 생성에 실패했습니다.' },
      );
    } finally {
      setIsSimulating(false);
    }
  };

  const activeRuleCount = rules.filter((r) => r.is_active).length;

  return (
    <div className="app-container">
      <Header
        onRefresh={loadData}
        onSimulate={handleSimulate}
        onOpenAlerts={() => setIsAlertsOpen(true)}
        onOpenSettings={() => setIsSettingsOpen(true)}
        alertCount={histories.length}
        activeRuleCount={activeRuleCount}
        isLoading={isLoading}
        isSimulating={isSimulating}
        isConnected={error === null}
        lastUpdated={lastUpdated}
        version={systemInfo?.version}
        windows={TIME_WINDOWS}
        windowIndex={windowIndex}
        onWindowChange={setWindowIndex}
      />

      <main className="main-content">
        <ConnectionBanner error={error} onOpenSettings={() => setIsSettingsOpen(true)} />
        <KPICards metrics={metrics} windowLabel={timeWindow.label} />
        <Suspense fallback={<div className="charts-grid"><div className="card chart-placeholder" /><div className="card chart-placeholder" /></div>}>
          <Charts timeseries={timeseries} breakdown={breakdown} windowLabel={timeWindow.label} />
        </Suspense>
        <TraceTable
          traces={traces}
          total={traceTotal}
          selectedProvider={selectedProvider}
          selectedStatus={selectedStatus}
          page={page}
          pageSize={PAGE_SIZE}
          onProviderChange={(value) => {
            setPage(0);
            setSelectedProvider(value);
          }}
          onStatusChange={(value) => {
            setPage(0);
            setSelectedStatus(value);
          }}
          onPageChange={setPage}
          onSelectTrace={setSelectedTrace}
        />
      </main>

      <TraceDetailModal trace={selectedTrace} onClose={() => setSelectedTrace(null)} />

      <AlertsModal
        isOpen={isAlertsOpen}
        onClose={() => setIsAlertsOpen(false)}
        rules={rules}
        histories={histories}
        onRefresh={loadData}
      />

      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        systemInfo={systemInfo}
        onSaved={loadData}
      />
    </div>
  );
};
