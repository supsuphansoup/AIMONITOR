import React from 'react';
import { AlertOctagon, Database, Tag, X } from 'lucide-react';
import { TraceEvent } from '../types';
import { formatCost, formatDateTime, formatMs, formatNumber, NOT_COMPUTABLE } from '../format';
import { useModalFocus } from './useModalFocus';

interface TraceDetailModalProps {
  trace: TraceEvent | null;
  onClose: () => void;
}

const COST_SOURCE_LABEL: Record<string, string> = {
  custom: '호출자 제공 값',
  pricing_table: '단가표 기준 계산',
  local_free: '로컬 실행 (과금 없음)',
  unknown: '단가 정보 없음',
};

export const TraceDetailModal: React.FC<TraceDetailModalProps> = ({ trace, onClose }) => {
  const dialogRef = useModalFocus(trace !== null, onClose);

  if (!trace) return null;

  const hasCache = trace.cache_read_tokens > 0 || trace.cache_write_tokens > 0;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-content"
        role="dialog"
        ref={dialogRef}
        tabIndex={-1}
        aria-modal="true"
        aria-labelledby="trace-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <div>
            <h3 id="trace-title" style={{ fontSize: 'var(--font-title)', fontWeight: 600 }}>Trace Details</h3>
            <p style={{ fontSize: '12px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
              ID: {trace.trace_id}
            </p>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="닫기">
            <X size={20} />
          </button>
        </div>

        <div style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }}>
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
          <span style={{ fontSize: '12px', color: 'var(--text-muted)', marginLeft: 'auto' }}>
            {formatDateTime(trace.timestamp)}
          </span>
        </div>

        {trace.error_message && (
          <div className="error-box">
            <AlertOctagon size={18} />
            <div>
              <strong>Error Details:</strong> {trace.error_message}
            </div>
          </div>
        )}

        <div className="metric-grid">
          <div>
            <span className="metric-label">Latency</span>
            <p className="metric-value">{formatMs(trace.latency_ms)}</p>
          </div>
          <div>
            <span className="metric-label">Total Tokens</span>
            <p className="metric-value">{formatNumber(trace.total_tokens)}</p>
            <span className="metric-note">
              Prompt {formatNumber(trace.prompt_tokens)} / Completion{' '}
              {formatNumber(trace.completion_tokens)}
            </span>
          </div>
          <div>
            <span className="metric-label">Cost</span>
            <p
              className="metric-value"
              style={{
                color:
                  trace.cost_source === 'unknown' ? 'var(--text-faint)' : 'var(--text-main)',
              }}
            >
              {formatCost(trace.cost_usd, trace.cost_source, 6)}
            </p>
            <span className="metric-note">
              {COST_SOURCE_LABEL[trace.cost_source] ?? trace.cost_source}
            </span>
          </div>
        </div>

        {hasCache && (
          <div className="metric-grid">
            <div>
              <span className="metric-label">
                <Database size={12} /> Cache Read
              </span>
              <p className="metric-value">{formatNumber(trace.cache_read_tokens)}</p>
              <span className="metric-note">할인 단가로 과금</span>
            </div>
            <div>
              <span className="metric-label">Cache Write</span>
              <p className="metric-value">{formatNumber(trace.cache_write_tokens)}</p>
              <span className="metric-note">할증 단가로 과금</span>
            </div>
            <div>
              <span className="metric-label">Session</span>
              <p className="metric-value">{trace.session_id ?? '-'}</p>
            </div>
          </div>
        )}

        {trace.cost_source === 'unknown' && (
          <div className="notice-box">
            {`'${trace.model_name}' 의 단가 정보가 없어 비용이 ${NOT_COMPUTABLE} 상태입니다. `}
            단가표에 모델을 추가하거나 수집 시 <code>cost_usd</code> 를 직접 전달하세요.
          </div>
        )}

        {trace.tags && (
          <div>
            <div className="section-label">
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

        {trace.metadata_json &&
          (() => {
            let displayContent: string;
            try {
              displayContent = JSON.stringify(JSON.parse(trace.metadata_json), null, 2);
            } catch {
              displayContent = trace.metadata_json;
            }
            return (
              <div>
                <div className="section-label">
                  Custom Metadata Payload
                  <span className="metric-note">프롬프트·응답 필드는 수집 시 제거됩니다</span>
                </div>
                <pre className="code-block">{displayContent}</pre>
              </div>
            );
          })()}
      </div>
    </div>
  );
};
