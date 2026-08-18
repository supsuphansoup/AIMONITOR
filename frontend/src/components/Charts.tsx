import React from 'react';
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  BarChart,
  Bar,
  CartesianGrid,
} from 'recharts';
import { TimeseriesPoint, ModelBreakdown } from '../types';

interface ChartsProps {
  timeseries: TimeseriesPoint[];
  breakdown: ModelBreakdown[];
}

export const Charts: React.FC<ChartsProps> = ({ timeseries, breakdown }) => {
  // Format timestamp for chart X-Axis
  const formattedTimeseries = timeseries.map((pt) => ({
    ...pt,
    time: new Date(pt.timestamp).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
    }),
  }));

  return (
    <div className="charts-grid">
      {/* Time-series Latency & Traffic */}
      <div className="card">
        <div className="card-title">
          <span>Latency & Ingestion Trend</span>
          <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Last 24 Hours</span>
        </div>
        <div style={{ width: '100%', height: 260 }}>
          {formattedTimeseries.length === 0 ? (
            <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-faint)' }}>
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
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis dataKey="time" stroke="#64748b" fontSize={11} tickLine={false} />
                <YAxis stroke="#64748b" fontSize={11} tickLine={false} unit="ms" />
                <Tooltip
                  contentStyle={{
                    backgroundColor: '#0f172a',
                    borderColor: '#334155',
                    borderRadius: '6px',
                    fontSize: '12px',
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="avg_latency_ms"
                  name="Avg Latency (ms)"
                  stroke="#06b6d4"
                  strokeWidth={2}
                  fillOpacity={1}
                  fill="url(#latencyGradient)"
                />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {/* Model & Cost Breakdown */}
      <div className="card">
        <div className="card-title">
          <span>Model Usage & Cost</span>
        </div>
        <div style={{ width: '100%', height: 260 }}>
          {breakdown.length === 0 ? (
            <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-faint)' }}>
              No model breakdown data.
            </div>
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
                  width={90}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: '#0f172a',
                    borderColor: '#334155',
                    borderRadius: '6px',
                    fontSize: '12px',
                  }}
                  formatter={(value: any, name: string) => [
                    name === 'total_cost_usd' ? `$${Number(value).toFixed(4)}` : value,
                    name === 'total_cost_usd' ? 'Cost ($)' : 'Calls',
                  ]}
                />
                <Bar dataKey="count" fill="#3b82f6" radius={[0, 4, 4, 0]} name="Calls" />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>
    </div>
  );
};
