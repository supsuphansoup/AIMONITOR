import React, { useState } from 'react';
import { ChevronLeft, ChevronRight, Download, Filter } from 'lucide-react';
import { api } from '../api';
import { TraceEvent } from '../types';
import { formatCost, formatMs, formatNumber, formatTime } from '../format';

interface TraceTableProps {
  traces: TraceEvent[];
  total: number;
  selectedProvider: string;
  selectedStatus: string;
  page: number;
  pageSize: number;
  onProviderChange: (provider: string) => void;
  onStatusChange: (status: string) => void;
  onPageChange: (page: number) => void;
  onSelectTrace: (trace: TraceEvent) => void;
}

function rowTint(status: string): string | undefined {
  if (status === 'ERROR') return 'rgba(239, 68, 68, 0.06)';
  if (status === 'TIMEOUT' || status === 'RATE_LIMITED') return 'rgba(245, 158, 11, 0.06)';
  return undefined;
}

function badgeClass(status: string): string {
  if (status === 'SUCCESS') return 'badge badge-success';
  if (status === 'TIMEOUT' || status === 'RATE_LIMITED') return 'badge badge-warning';
  return 'badge badge-error';
}

export const TraceTable: React.FC<TraceTableProps> = ({
  traces,
  total,
  selectedProvider,
  selectedStatus,
  page,
  pageSize,
  onProviderChange,
  onStatusChange,
  onPageChange,
  onSelectTrace,
}) => {
  const [isExporting, setIsExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  // X-Total-Count gives a real page count instead of inferring "there may be
  // more" from a full page.
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const hasNextPage = page + 1 < pageCount;

  const handleExport = async () => {
    setIsExporting(true);
    setExportError(null);
    try {
      const blob = await api.exportCsv({ provider: selectedProvider || undefined });
      // Fetched as a Blob rather than linked directly, because a plain download
      // link cannot carry the API key header.
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `aimonitor-traces-${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch {
      setExportError('CSV 내보내기에 실패했습니다.');
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <div className="card">
      <div className="card-title">
        <span>Live Trace Stream</span>
        <div className="filter-bar">
          <button
            className="btn btn-secondary pager-btn"
            onClick={handleExport}
            disabled={isExporting || total === 0}
            title="현재 필터 조건으로 CSV 내보내기"
          >
            <Download size={13} />
            {isExporting ? '내보내는 중...' : 'CSV'}
          </button>
          <Filter size={14} color="var(--text-muted)" />
          <select
            className="select-input"
            value={selectedProvider}
            onChange={(e) => onProviderChange(e.target.value)}
            aria-label="Provider filter"
          >
            <option value="">All Providers</option>
            <option value="openai">OpenAI</option>
            <option value="anthropic">Anthropic</option>
            <option value="gemini">Google Gemini</option>
            <option value="ollama">Ollama / Local</option>
          </select>

          <select
            className="select-input"
            value={selectedStatus}
            onChange={(e) => onStatusChange(e.target.value)}
            aria-label="Status filter"
          >
            <option value="">All Statuses</option>
            <option value="SUCCESS">Success</option>
            <option value="ERROR">Error</option>
            <option value="TIMEOUT">Timeout</option>
            <option value="RATE_LIMITED">Rate limited</option>
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
                <td colSpan={7} className="table-empty">
                  {page > 0
                    ? '이 페이지에는 트레이스가 없습니다.'
                    : 'No traces recorded yet. Click "Generate Demo Traffic" to stream live events!'}
                </td>
              </tr>
            ) : (
              traces.map((trace) => (
                <tr
                  key={trace.id}
                  onClick={() => onSelectTrace(trace)}
                  style={{ backgroundColor: rowTint(trace.status) }}
                >
                  <td>
                    <span className={badgeClass(trace.status)}>{trace.status}</span>
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
                  <td>
                    {formatNumber(trace.total_tokens)}
                    {trace.cache_read_tokens > 0 && (
                      <span className="cell-note" title="프롬프트 캐시에서 읽은 토큰">
                        cache {formatNumber(trace.cache_read_tokens)}
                      </span>
                    )}
                  </td>
                  <td>{formatMs(trace.latency_ms)}</td>
                  <td
                    className={trace.cost_source === 'unknown' ? 'cost-unknown' : undefined}
                    style={{
                      color:
                        trace.cost_source === 'unknown'
                          ? 'var(--text-faint)'
                          : 'var(--status-success)',
                    }}
                    title={
                      trace.cost_source === 'unknown'
                        ? `${trace.model_name} 단가 정보가 없어 비용을 계산할 수 없습니다`
                        : undefined
                    }
                  >
                    {formatCost(trace.cost_usd, trace.cost_source)}
                  </td>
                  <td style={{ color: 'var(--text-muted)' }}>{formatTime(trace.timestamp)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {exportError && <div className="banner banner-error">{exportError}</div>}

      <div className="pager">
        <span className="pager-label">
          {total > 0
            ? `${formatNumber(page * pageSize + 1)}–${formatNumber(page * pageSize + traces.length)} / ${formatNumber(total)}건 · ${page + 1}/${pageCount} 페이지`
            : '표시할 트레이스 없음'}
        </span>
        <button
          className="btn btn-secondary pager-btn"
          disabled={page === 0}
          onClick={() => onPageChange(page - 1)}
        >
          <ChevronLeft size={14} /> 이전
        </button>
        <button
          className="btn btn-secondary pager-btn"
          disabled={!hasNextPage}
          onClick={() => onPageChange(page + 1)}
        >
          다음 <ChevronRight size={14} />
        </button>
      </div>
    </div>
  );
};
