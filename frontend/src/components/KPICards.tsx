import React from 'react';
import { OverviewMetrics } from '../types';
import { Zap, Clock, DollarSign, AlertTriangle } from 'lucide-react';

interface KPICardsProps {
  metrics: OverviewMetrics | null;
}

export const KPICards: React.FC<KPICardsProps> = ({ metrics }) => {
  const totalReq = metrics?.total_requests ?? 0;
  const avgLat = metrics?.avg_latency_ms ?? 0;
  const p95Lat = metrics?.p95_latency_ms ?? 0;
  const cost = metrics?.total_cost_usd ?? 0;
  const errorRate = metrics?.error_rate_pct ?? 0;
  const tokens = metrics?.total_tokens ?? 0;

  return (
    <div className="kpi-grid">
      <div className="kpi-card">
        <div className="kpi-header">
          <span>Total Ingested Calls</span>
          <Zap size={16} color="#3b82f6" />
        </div>
        <div className="kpi-value">{totalReq.toLocaleString()}</div>
        <div className="kpi-sub">
          <span>{tokens.toLocaleString()} total tokens processed</span>
        </div>
      </div>

      <div className="kpi-card">
        <div className="kpi-header">
          <span>Latency (Avg / P95)</span>
          <Clock size={16} color="#06b6d4" />
        </div>
        <div className="kpi-value">
          {avgLat} <span style={{ fontSize: '15px', color: 'var(--text-muted)' }}>ms</span>
        </div>
        <div className="kpi-sub">
          <span>P95: {p95Lat} ms | P99: {metrics?.p99_latency_ms ?? 0} ms</span>
        </div>
      </div>

      <div className="kpi-card">
        <div className="kpi-header">
          <span>Estimated Total Cost</span>
          <DollarSign size={16} color="#10b981" />
        </div>
        <div className="kpi-value">${cost.toFixed(4)}</div>
        <div className="kpi-sub">
          <span>Prompt: {metrics?.prompt_tokens?.toLocaleString() ?? 0} tkn</span>
        </div>
      </div>

      <div className="kpi-card">
        <div className="kpi-header">
          <span>Error Rate</span>
          <AlertTriangle size={16} color={errorRate > 0 ? '#ef4444' : '#10b981'} />
        </div>
        <div className="kpi-value" style={{ color: errorRate > 0 ? 'var(--status-error)' : 'var(--text-main)' }}>
          {errorRate.toFixed(1)}%
        </div>
        <div className="kpi-sub">
          <span>{metrics?.error_count ?? 0} failed / {metrics?.success_count ?? 0} success</span>
        </div>
      </div>
    </div>
  );
};
