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
}

const TOOLTIP_STYLE = {
  backgroundColor: '#0f172a',
  borderColor: '#334155',
  borderRadius: '6px',
  fontSize: '12px',
};

export const Charts: React.FC<ChartsProps> = ({ timeseries, breakdown, windowLabel }) => {
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
          <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
            Last {windowLabel}
            {hasPartialBucket && (
              <span className="kpi-flag" title="서버 조회 한도로 가장 오래된 구간이 잘렸습니다">
                {' '}· 구간 일부 누락
              </span>
            )}
          </span>
        </div>
        <div style={{ width: '100%', height: 260 }}>
          {formattedTimeseries.length === 0 ? (
            <div className="chart-empty">
              No time-series data yet. Generate demo traffic to view charts.
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={formattedTimeseries}>
                <defs>
                  <linearGradient id="latencyGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#06b6d4" stopOpacity={0.4} />
                    <stop offset="95%" stopColor="#06b6d4" stopOpacity={0.0} />
                  </linearGradient>
                  <linearGradient id="p95Gradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#a855f7" stopOpacity={0.25} />
                    <stop offset="95%" stopColor="#a855f7" stopOpacity={0.0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis dataKey="time" stroke="#64748b" fontSize={11} tickLine={false} />
                <YAxis stroke="#64748b" fontSize={11} tickLine={false} unit="ms" />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <Legend wrapperStyle={{ fontSize: '11px' }} />
                <Area
                  type="monotone"
                  dataKey="avg_latency_ms"
                  name="Avg"
                  stroke="#06b6d4"
                  strokeWidth={2}
                  fillOpacity={1}
                  fill="url(#latencyGradient)"
                />
                {/* P95 is where a latency regression shows up first. */}
                <Area
                  type="monotone"
                  dataKey="p95_latency_ms"
                  name="P95"
                  stroke="#a855f7"
                  strokeWidth={1.5}
                  fillOpacity={1}
                  fill="url(#p95Gradient)"
                />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-title">
          <span>Model Usage &amp; Cost</span>
          <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Last {windowLabel}</span>
        </div>
        <div style={{ width: '100%', height: 260 }}>
          {breakdown.length === 0 ? (
            <div className="chart-empty">No model breakdown data.</div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={breakdown} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis type="number" stroke="#64748b" fontSize={11} tickLine={false} />
                <YAxis
                  dataKey="model_name"
                  type="category"
                  stroke="#94a3b8"
                  fontSize={11}
                  tickLine={false}
                  width={110}
                />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  formatter={(value: any, name: string) =>
                    name === 'Cost ($)' ? [`$${Number(value).toFixed(4)}`, name] : [value, name]
                  }
                />
                <Legend wrapperStyle={{ fontSize: '11px' }} />
                <Bar dataKey="count" fill="#3b82f6" radius={[0, 4, 4, 0]} name="Calls" />
                <Bar dataKey="error_count" fill="#ef4444" radius={[0, 4, 4, 0]} name="Errors" />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>
    </div>
  );
};
