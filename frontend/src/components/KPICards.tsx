import React from 'react';
import { AlertTriangle, Clock, Database, DollarSign, Zap } from 'lucide-react';
import { OverviewMetrics } from '../types';
import { formatMs, formatNumber, formatUsd, NOT_COMPUTABLE } from '../format';

interface KPICardsProps {
  metrics: OverviewMetrics | null;
  windowLabel: string;
}

export const KPICards: React.FC<KPICardsProps> = ({ metrics, windowLabel }) => {
  const totalReq = metrics?.total_requests ?? 0;
  const errorRate = metrics?.error_rate_pct ?? 0;
  const unpriced = metrics?.cost_unknown_count ?? 0;
  const cacheRead = metrics?.cache_read_tokens ?? 0;
  const cacheWrite = metrics?.cache_write_tokens ?? 0;
  const allUnpriced = totalReq > 0 && unpriced === totalReq;

  return (
    <div className="kpi-grid">
      <div className="kpi-card">
        <div className="kpi-header">
          <span>Total Ingested Calls · {windowLabel}</span>
          <Zap size={16} color="#3b82f6" />
        </div>
        <div className="kpi-value">{formatNumber(totalReq)}</div>
        <div className="kpi-sub">
          <span>{formatNumber(metrics?.total_tokens)} total tokens processed</span>
        </div>
      </div>

      <div className="kpi-card">
        <div className="kpi-header">
          <span>Latency (Avg / P95)</span>
          <Clock size={16} color="#06b6d4" />
        </div>
        <div className="kpi-value">{formatMs(metrics?.avg_latency_ms)}</div>
        <div className="kpi-sub">
          <span>
            P95 {formatMs(metrics?.p95_latency_ms)} · P99 {formatMs(metrics?.p99_latency_ms)}
          </span>
          {metrics?.latency_sampled && (
            <span className="kpi-flag" title="표본이 잘려 백분위수는 근사치입니다">
              표본 추정
            </span>
          )}
        </div>
      </div>

      <div className="kpi-card">
        <div className="kpi-header">
          <span>Estimated Total Cost</span>
          <DollarSign size={16} color="#10b981" />
        </div>
        <div className="kpi-value">
          {/* A missing rate card is reported, never rounded down to $0. */}
          {allUnpriced ? NOT_COMPUTABLE : formatUsd(metrics?.total_cost_usd ?? 0)}
        </div>
        <div className="kpi-sub">
          {unpriced > 0 ? (
            <span className="kpi-flag" title="단가 정보가 없는 모델은 합계에서 제외됩니다">
              {formatNumber(unpriced)}건 {NOT_COMPUTABLE}
            </span>
          ) : (
            <span>Prompt {formatNumber(metrics?.prompt_tokens)} tkn</span>
          )}
        </div>
      </div>

      <div className="kpi-card">
        <div className="kpi-header">
          <span>Error Rate</span>
          <AlertTriangle size={16} color={errorRate > 0 ? '#ef4444' : '#10b981'} />
        </div>
        <div
          className="kpi-value"
          style={{ color: errorRate > 0 ? 'var(--status-error)' : 'var(--text-main)' }}
        >
          {errorRate.toFixed(1)}%
        </div>
        <div className="kpi-sub">
          <span>
            {formatNumber(metrics?.error_count)} failed / {formatNumber(metrics?.success_count)} success
          </span>
        </div>
      </div>

      {(cacheRead > 0 || cacheWrite > 0) && (
        <div className="kpi-card">
          <div className="kpi-header">
            <span>Prompt Cache</span>
            <Database size={16} color="#a855f7" />
          </div>
          <div className="kpi-value">{formatNumber(cacheRead)}</div>
          <div className="kpi-sub">
            {/* Read and write are billed at different rates, so they stay split. */}
            <span>read tokens · write {formatNumber(cacheWrite)}</span>
          </div>
        </div>
      )}
    </div>
  );
};
