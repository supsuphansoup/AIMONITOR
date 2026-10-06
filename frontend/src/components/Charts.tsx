import React from 'react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { ModelBreakdown, TimeseriesPoint } from '../types';
import { formatTime } from '../format';

interface ChartsProps {
  timeseries: TimeseriesPoint[];
  breakdown: ModelBreakdown[];
  windowLabel: string;
  showDemoTraffic: boolean;
}

const TOOLTIP_STYLE = {
  backgroundColor: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
  borderRadius: 'var(--radius-sm)',
  color: 'var(--text-main)',
  fontSize: 'var(--font-sm)',
};

export const Charts: React.FC<ChartsProps> = ({ timeseries, breakdown, windowLabel, showDemoTraffic }) => {
  const formattedTimeseries = timeseries.map((pt) => ({
    ...pt,
    // Backend timestamps carry a UTC offset, so this lands in the viewer locale.
    time: formatTime(pt.timestamp),
  }));

  // The oldest bucket is a floor rather than a total when the server hit its row
  // cap. Drawing it like the rest would understate a spike at the window edge.
  const hasPartialBucket = timeseries.some((pt) => pt.partial);

  return (
    <div className="charts-grid">
      <div className="card">
        <div className="card-title">
          <span>Latency &amp; Ingestion Trend</span>
          <span className="chart-range">
            Last {windowLabel}
            {hasPartialBucket && (
              <span className="kpi-flag" title="서버 조회 한도로 가장 오래된 구간이 잘렸습니다">
                {' '}· 구간 일부 누락
              </span>
            )}
          </span>
        </div>
        <div className="chart-frame">
          {formattedTimeseries.length === 0 ? (
            <div className="chart-empty">
              {showDemoTraffic
                ? 'No time-series data yet. Generate demo traffic to view charts.'
                : 'No time-series data yet. Connect the SDK or wait for incoming traffic.'}
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={formattedTimeseries} margin={{ top: 8, right: 28, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="latencyGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="var(--accent-blue)" stopOpacity={0.18} />
                    <stop offset="95%" stopColor="var(--accent-blue)" stopOpacity={0.0} />
                  </linearGradient>
                  <linearGradient id="p95Gradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="var(--chart-secondary)" stopOpacity={0.08} />
                    <stop offset="95%" stopColor="var(--chart-secondary)" stopOpacity={0.0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border-color)" vertical={false} />
                <XAxis
                  dataKey="time"
                  stroke="var(--text-faint)"
                  fontSize={11}
                  tickLine={false}
                  axisLine={false}
                  minTickGap={36}
                  padding={{ left: 12, right: 24 }}
                />
                <YAxis stroke="var(--text-faint)" fontSize={11} tickLine={false} axisLine={false} unit="ms" width={58} />
                <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={{ color: 'var(--text-main)' }} cursor={{ stroke: 'var(--border-color)' }} />
                <Legend wrapperStyle={{ fontSize: '11px' }} />
                <Area
                  type="monotone"
                  dataKey="avg_latency_ms"
                  name="Avg"
                  stroke="var(--accent-blue)"
                  strokeWidth={2}
                  fillOpacity={1}
                  fill="url(#latencyGradient)"
                  isAnimationActive={false}
                />
                {/* P95 is where a latency regression shows up first. */}
                <Area
                  type="monotone"
                  dataKey="p95_latency_ms"
                  name="P95"
                  stroke="var(--chart-secondary)"
                  strokeWidth={1.5}
                  strokeDasharray="4 3"
                  fillOpacity={1}
                  fill="url(#p95Gradient)"
                  isAnimationActive={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-title">
          <span>Model Calls &amp; Errors</span>
          <span className="chart-range">Last {windowLabel}</span>
        </div>
        <div className="chart-frame">
          {breakdown.length === 0 ? (
            <div className="chart-empty">No model breakdown data.</div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={breakdown} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border-color)" horizontal={false} />
                <XAxis type="number" stroke="var(--text-faint)" fontSize={11} tickLine={false} axisLine={false} />
                <YAxis
                  dataKey="model_name"
                  type="category"
                  stroke="var(--text-muted)"
                  fontSize={11}
                  tickLine={false}
                  axisLine={false}
                  width={110}
                />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  labelStyle={{ color: 'var(--text-main)' }}
                  cursor={{ fill: 'var(--bg-subtle)' }}
                />
                <Legend wrapperStyle={{ fontSize: '11px' }} />
                <Bar dataKey="count" fill="var(--accent-blue)" radius={[0, 4, 4, 0]} name="Calls" isAnimationActive={false} />
                <Bar dataKey="error_count" fill="var(--status-error)" radius={[0, 4, 4, 0]} name="Errors" isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>
    </div>
  );
};
