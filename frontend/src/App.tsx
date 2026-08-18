import React, { useState, useEffect, useCallback } from 'react';
import { Header } from './components/Header';
import { KPICards } from './components/KPICards';
import { Charts } from './components/Charts';
import { TraceTable } from './components/TraceTable';
import { TraceDetailModal } from './components/TraceDetailModal';
import { AlertsModal } from './components/AlertsModal';
import {
  TraceEvent,
  OverviewMetrics,
  TimeseriesPoint,
  ModelBreakdown,
  AlertRule,
  AlertHistory,
} from './types';

export const App: React.FC = () => {
  const [metrics, setMetrics] = useState<OverviewMetrics | null>(null);
  const [timeseries, setTimeseries] = useState<TimeseriesPoint[]>([]);
  const [breakdown, setBreakdown] = useState<ModelBreakdown[]>([]);
  const [traces, setTraces] = useState<TraceEvent[]>([]);
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [histories, setHistories] = useState<AlertHistory[]>([]);
  const [isAlertsOpen, setIsAlertsOpen] = useState<boolean>(false);
  const [selectedProvider, setSelectedProvider] = useState<string>('');
  const [selectedStatus, setSelectedStatus] = useState<string>('');
  const [selectedTrace, setSelectedTrace] = useState<TraceEvent | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [isSimulating, setIsSimulating] = useState<boolean>(false);

  const loadData = useCallback(async () => {
    setIsLoading(true);
    try {
      // 1. Fetch Overview
      const overviewRes = await fetch('/api/v1/metrics/overview');
      if (overviewRes.ok) {
        setMetrics(await overviewRes.json());
      }

      // 2. Fetch Timeseries
      const timeseriesRes = await fetch('/api/v1/metrics/timeseries?hours_back=24&interval_minutes=5');
      if (timeseriesRes.ok) {
        setTimeseries(await timeseriesRes.json());
      }

      // 3. Fetch Breakdown
      const breakdownRes = await fetch('/api/v1/metrics/breakdown');
      if (breakdownRes.ok) {
        setBreakdown(await breakdownRes.json());
      }

      // 4. Fetch Traces
      let traceUrl = '/api/v1/traces?limit=50';
      if (selectedProvider) traceUrl += `&provider=${selectedProvider}`;
      if (selectedStatus) traceUrl += `&status=${selectedStatus}`;
      const traceRes = await fetch(traceUrl);
      if (traceRes.ok) {
        setTraces(await traceRes.json());
      }

      // 5. Fetch Alert Rules & Histories
      const rulesRes = await fetch('/api/v1/alerts/rules');
      if (rulesRes.ok) {
        setRules(await rulesRes.json());
      }

      const histRes = await fetch('/api/v1/alerts/history');
      if (histRes.ok) {
        setHistories(await histRes.json());
      }
    } catch (err) {
      console.error('Failed to fetch monitoring data:', err);
    } finally {
      setIsLoading(false);
    }
  }, [selectedProvider, selectedStatus]);

  // Initial load and periodic polling every 5s
  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 5000);
    return () => clearInterval(interval);
  }, [loadData]);

  // Generate realistic demo traffic
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
      const isError = Math.random() < 0.15; // 15% error chance
      return {
        provider: item.provider,
        model_name: item.model,
        prompt_tokens: item.pTkn + Math.floor(Math.random() * 200),
        completion_tokens: isError ? 0 : item.cTkn + Math.floor(Math.random() * 100),
        latency_ms: item.lat + Math.floor(Math.random() * 150),
        status: isError ? 'ERROR' : 'SUCCESS',
        error_message: isError ? 'HTTP 429: Rate limit exceeded or upstream timeout' : undefined,
        tags: ['demo-feed', `batch-${i}`],
        metadata: { client: 'simulation-engine', env: 'production' },
      };
    });

    try {
      await fetch('/api/v1/traces/batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ traces: batch }),
      });
      // Trigger evaluation after new traffic
      await fetch('/api/v1/alerts/evaluate', { method: 'POST' });
      await loadData();
    } catch (e) {
      console.error('Simulation error:', e);
    } finally {
      setIsSimulating(false);
    }
  };

  return (
    <div className="app-container">
      <Header
        onRefresh={loadData}
        onSimulate={handleSimulate}
        onOpenAlerts={() => setIsAlertsOpen(true)}
        alertCount={histories.length}
        isLoading={isLoading}
        isSimulating={isSimulating}
      />

      <main className="main-content">
        <KPICards metrics={metrics} />
        <Charts timeseries={timeseries} breakdown={breakdown} />
        <TraceTable
          traces={traces}
          selectedProvider={selectedProvider}
          selectedStatus={selectedStatus}
          onProviderChange={setSelectedProvider}
          onStatusChange={setSelectedStatus}
          onSelectTrace={setSelectedTrace}
        />
      </main>

      <TraceDetailModal
        trace={selectedTrace}
        onClose={() => setSelectedTrace(null)}
      />

      <AlertsModal
        isOpen={isAlertsOpen}
        onClose={() => setIsAlertsOpen(false)}
        rules={rules}
        histories={histories}
        onRefresh={loadData}
      />
    </div>
  );
};
