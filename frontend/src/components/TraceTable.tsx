import React from 'react';
import { TraceEvent } from '../types';
import { Filter } from 'lucide-react';

interface TraceTableProps {
  traces: TraceEvent[];
  selectedProvider: string;
  selectedStatus: string;
  onProviderChange: (provider: string) => void;
  onStatusChange: (status: string) => void;
  onSelectTrace: (trace: TraceEvent) => void;
}

export const TraceTable: React.FC<TraceTableProps> = ({
  traces,
  selectedProvider,
  selectedStatus,
  onProviderChange,
  onStatusChange,
  onSelectTrace,
}) => {
  return (
    <div className="card">
      <div className="card-title">
        <span>Live Trace Stream ({traces.length})</span>
        {/* Filters */}
        <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
          <Filter size={14} color="var(--text-muted)" />
          <select
            value={selectedProvider}
            onChange={(e) => onProviderChange(e.target.value)}
            style={{
              background: 'var(--bg-subtle)',
              color: 'var(--text-main)',
              border: '1px solid var(--border-color)',
              borderRadius: '4px',
              padding: '4px 8px',
              fontSize: '12px',
            }}
          >
            <option value="">All Providers</option>
            <option value="openai">OpenAI</option>
            <option value="anthropic">Anthropic</option>
            <option value="gemini">Google Gemini</option>
            <option value="ollama">Ollama / Local</option>
          </select>

          <select
            value={selectedStatus}
            onChange={(e) => onStatusChange(e.target.value)}
            style={{
              background: 'var(--bg-subtle)',
              color: 'var(--text-main)',
              border: '1px solid var(--border-color)',
              borderRadius: '4px',
              padding: '4px 8px',
              fontSize: '12px',
            }}
          >
            <option value="">All Statuses</option>
            <option value="SUCCESS">Success</option>
            <option value="ERROR">Error</option>
          </select>
        </div>
      </div>

      <div className="table-container">
        <table className="data-table">
          <thead>
            <tr>
              <th>Status</th>
              <th>Trace ID</th>
              <th>Provider / Model</th>
              <th>Tokens</th>
              <th>Latency</th>
              <th>Cost (USD)</th>
              <th>Time</th>
            </tr>
          </thead>
          <tbody>
            {traces.length === 0 ? (
              <tr>
                <td colSpan={7} style={{ textAlign: 'center', padding: '32px', color: 'var(--text-faint)' }}>
                  No traces recorded yet. Click "Generate Demo Traffic" to stream live events!
                </td>
              </tr>
            ) : (
              traces.map((trace) => (
                <tr
                  key={trace.id}
                  onClick={() => onSelectTrace(trace)}
                  style={{
                    backgroundColor:
                      trace.status === 'ERROR'
                        ? 'rgba(239, 68, 68, 0.06)'
                        : trace.status === 'TIMEOUT' || trace.status === 'RATE_LIMITED'
                        ? 'rgba(245, 158, 11, 0.06)'
                        : undefined,
                  }}
                >
                  <td>
                    <span
                      className={`badge ${
                        trace.status === 'SUCCESS'
                          ? 'badge-success'
                          : trace.status === 'TIMEOUT' || trace.status === 'RATE_LIMITED'
                          ? 'badge-warning'
                          : 'badge-error'
                      }`}
                    >
                      {trace.status}
                    </span>
                  </td>
                  <td style={{ color: 'var(--accent-cyan)' }}>
                    {trace.trace_id.length > 14
                      ? `${trace.trace_id.substring(0, 14)}...`
                      : trace.trace_id}
                  </td>
                  <td>
                    <span style={{ color: 'var(--text-muted)' }}>{trace.provider} / </span>
                    <strong>{trace.model_name}</strong>
                  </td>
                  <td>{trace.total_tokens.toLocaleString()}</td>
                  <td>{trace.latency_ms} ms</td>
                  <td style={{ color: 'var(--status-success)' }}>${trace.cost_usd.toFixed(5)}</td>
                  <td style={{ color: 'var(--text-muted)' }}>
                    {new Date(trace.timestamp).toLocaleTimeString()}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
