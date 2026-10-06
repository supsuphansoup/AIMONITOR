import React from 'react';
import { Bot, KeyRound } from 'lucide-react';
import { formatDateTime, formatNumber, formatProviderSpend, formatUsd } from '../format';
import { ConnectedAIItem, ConnectedAISummary, UsageBlock } from '../types';

interface ConnectedAICardsProps {
  summary: ConnectedAISummary | null;
  onOpenKeys: () => void;
  error?: { message: string; status?: number } | null;
  onRetry: () => void;
  onOpenSettings: () => void;
}

function costText(usage: UsageBlock, meaning: ConnectedAIItem['cost_meaning']): string {
  if (usage.cost_usd === null || (usage.cost_unknown > 0 && usage.cost_unknown === usage.requests)) return '계산 불가';
  const amount = formatUsd(usage.cost_usd);
  return `${amount}${meaning === 'api_equivalent' ? ' (API 환산)' : meaning === 'free' ? ' (로컬)' : ''}`
    + (usage.cost_unknown ? ` · ${usage.cost_unknown}건 계산 불가` : '');
}

const sources = { official: '공식', user_limit: '직접 입력 한도', unavailable: '확인 불가' };

export const ConnectedAICards: React.FC<ConnectedAICardsProps> = ({ summary, onOpenKeys, error, onRetry, onOpenSettings }) => {
  return <section className="connected-ai-section" aria-labelledby="connected-ai-title">
    <div className="section-heading">
      <div>
        <h2 id="connected-ai-title" className="card-title">이 PC에 연결된 AI</h2>
        <p className="metric-note">백엔드 PC의 연결 상태 · 공식 한도와 로컬 사용 기록을 함께 확인합니다.</p>
      </div>
      <button className="btn btn-secondary" onClick={onOpenKeys}><KeyRound size={14} /> 공급자 키 관리</button>
    </div>
    {error ? <div className="banner banner-error" role="alert">
      <span>연결 AI 정보를 확인할 수 없습니다.</span>
      {error.status === 401 || error.status === 403
        ? <button className="btn btn-secondary" onClick={onOpenSettings}>API 키 입력</button>
        : <button className="btn btn-secondary" onClick={onRetry}>다시 시도</button>}
    </div> : !summary ? <p className="empty-hint">연결 AI 정보를 불러오는 중…</p>
      : !summary.items.length ? <p className="empty-hint">연결된 AI가 없습니다. 공급자 API 키를 등록하거나 이 PC의 AI 도구에 로그인하세요.</p>
      : <>
        <p className="metric-note">{formatNumber(summary.total.connected_count)}개 연결
          {summary.total.hidden_count > 0 && ` · ${summary.total.hidden_count}개 숨김`} · 비용은 이 PC에 수집된 기록 기준이며 계정 전체 청구액과 다를 수 있습니다.</p>
        <div className="connected-ai-grid">{summary.items.map((item) => <article className="card connected-ai-card" key={item.key}>
          <div className="connected-ai-identity"><span className="connected-ai-icon"><Bot size={18} /></span>
            <div><h3>{item.display_name}</h3><p className="metric-note">{item.plan_name || '플랜 확인 불가'}</p></div>
          </div>
          <div className="connected-ai-quotas">{item.quota_windows.map((window) => <div className="connected-ai-quota" key={window.id}>
            <div className="section-heading"><span>{window.label} <span className="metric-note">· {sources[window.source]}</span></span>
              <strong>{window.remaining_pct === null ? '확인 불가' : `${window.remaining_pct.toFixed(0)}% 남음`}</strong></div>
            {window.remaining_pct !== null && <progress max={100} value={window.remaining_pct} aria-label={`${item.display_name} ${window.label} 남은 사용량`} />}
            <p className="metric-note">{window.source_label}{window.stale && ' · 오래된 값'}</p>
            {window.as_of && <p className="metric-note">{formatDateTime(window.as_of)} 기준</p>}
            {window.resets_at && <p className="metric-note">리셋 {formatDateTime(window.resets_at)}</p>}
            {window.note && <p className="metric-note">{window.note}</p>}
          </div>)}</div>
          <div className="connected-ai-context"><span className="metric-label">컨텍스트 (추정)</span>
            <p className="metric-value">{item.context
              ? `${formatNumber(item.context.tokens)} / ${item.context.max_tokens ? formatNumber(item.context.max_tokens) : '확인 불가'}`
              : '계산 불가'}</p>
            {item.context && <p className="metric-note">{item.context.model_name} · 마지막 요청 기준</p>}
          </div>
          <dl className="connected-ai-usage">
            <div><dt>오늘 로컬 기록</dt><dd>{formatNumber(item.usage.today.requests)}회 · {costText(item.usage.today, item.cost_meaning)}</dd></div>
            <div><dt>이번 달 로컬 기록</dt><dd>{formatNumber(item.usage.month.requests)}회 · {costText(item.usage.month, item.cost_meaning)}</dd></div>
            <div><dt>공식 청구/잔액</dt><dd>{formatProviderSpend(item.official_spend)}</dd></div>
          </dl>
          {item.official_spend?.as_of && <p className="metric-note">{item.official_spend.source_label} · {formatDateTime(item.official_spend.as_of)} 기준</p>}
          <footer className="connected-ai-footer"><p>{item.session_status}</p><p className="metric-note">{item.billing_note}</p></footer>
        </article>)}</div>
      </>}
  </section>;
};
