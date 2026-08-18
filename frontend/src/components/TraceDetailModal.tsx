import React, { useEffect } from 'react';
import { TraceEvent } from '../types';
import { X, AlertOctagon, Tag } from 'lucide-react';

interface TraceDetailModalProps {
  trace: TraceEvent | null;
  onClose: () => void;
}

export const TraceDetailModal: React.FC<TraceDetailModalProps> = ({ trace, onClose }) => {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  if (!trace) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <h3 style={{ fontSize: '17px', fontWeight: 600 }}>Trace Details</h3>
            <p style={{ fontSize: '12px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
              ID: {trace.trace_id}
            </p>
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'transparent',
              border: 'none',
              color: 'var(--text-muted)',
              cursor: 'pointer',
            }}
          >
            <X size={20} />
          </button>
        </div>

        {/* Status and Provider */}
        <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
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
          <span className="badge badge-provider">{trace.provider}</span>
          <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--accent-cyan)' }}>
            {trace.model_name}
          </span>
        </div>

        {/* Error message alert if exists */}
        {trace.error_message && (
          <div
            style={{
              background: 'rgba(239, 68, 68, 0.1)',
              border: '1px solid rgba(239, 68, 68, 0.3)',
              borderRadius: '6px',
              padding: '12px',
              display: 'flex',
              gap: '10px',
              color: '#f87171',
              fontSize: '13px',
            }}
          >
            <AlertOctagon size={18} />
            <div>
              <strong>Error Details:</strong> {trace.error_message}
            </div>
          </div>
        )}

        {/* Metric breakdown grid */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(3, 1fr)',
            gap: '12px',
            background: 'var(--bg-subtle)',
            padding: '14px',
            borderRadius: '6px',
          }}
        >
          <div>
            <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Latency</span>
            <p style={{ fontSize: '15px', fontWeight: 600, fontFamily: 'var(--font-mono)' }}>
              {trace.latency_ms} ms
            </p>
          </div>
          <div>
            <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Total Tokens</span>
            <p style={{ fontSize: '15px', fontWeight: 600, fontFamily: 'var(--font-mono)' }}>
              {trace.total_tokens.toLocaleString()}
            </p>
            <span style={{ fontSize: '10px', color: 'var(--text-faint)' }}>
              (Prompt: {trace.prompt_tokens} / Comp: {trace.completion_tokens})
            </span>
          </div>
          <div>
            <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Cost</span>
            <p style={{ fontSize: '15px', fontWeight: 600, fontFamily: 'var(--font-mono)', color: 'var(--status-success)' }}>
              ${trace.cost_usd.toFixed(6)}
            </p>
          </div>
        </div>

        {/* Tags */}
        {trace.tags && (
          <div>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px', display: 'flex', alignItems: 'center', gap: '6px' }}>
              <Tag size={13} /> Tags
            </div>
            <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
              {trace.tags.split(',').map((tag, idx) => (
                <span key={idx} className="badge badge-provider">
                  {tag.trim()}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Metadata JSON */}
        {trace.metadata_json && (() => {
          let displayContent: string;
          try {
            displayContent = JSON.stringify(JSON.parse(trace.metadata_json), null, 2);
          } catch {
            displayContent = trace.metadata_json;
          }
          return (
            <div>
              <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>
                Custom Metadata Payload
              </div>
              <pre className="code-block">
                {displayContent}
              </pre>
            </div>
          );
        })()}
      </div>
    </div>
  );
};
