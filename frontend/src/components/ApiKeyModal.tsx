import React, { useEffect, useState } from 'react';
import { KeyRound, RefreshCw, Trash2, X } from 'lucide-react';
import { api, ApiError } from '../api';
import { formatDateTime, formatProviderSpend } from '../format';
import { ApiKeyItem, ProviderSpend } from '../types';
import { ConnectionBanner } from './ConnectionBanner';
import { useModalFocus } from './useModalFocus';

interface ApiKeyModalProps {
  isOpen: boolean;
  onClose: () => void;
  onKeysChanged: () => void;
  onOpenSettings: () => void;
}

export const ApiKeyModal: React.FC<ApiKeyModalProps> = ({ isOpen, onClose, onKeysChanged, onOpenSettings }) => {
  const dialogRef = useModalFocus(isOpen, onClose);
  const [keys, setKeys] = useState<ApiKeyItem[]>([]);
  const [spend, setSpend] = useState<Record<string, ProviderSpend>>({});
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [notices, setNotices] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<{ message: string; status?: number } | null>(null);

  const showError = (err: unknown) => {
    const status = err instanceof ApiError ? err.status : undefined;
    setError({ status, message: status === 401 || status === 403
      ? '쓰기 권한이 있는 서버 API 키를 설정에서 입력하세요.'
      : '키 정보를 처리할 수 없습니다. 서버 연결과 키 저장 지원 여부를 확인하세요.' });
  };

  useEffect(() => {
    setInputs({});
    setNotices({});
    setError(null);
    if (!isOpen) return;
    const controller = new AbortController();
    setLoading(true);
    setKeys([]);
    setSpend({});
    api.listApiKeys(controller.signal).then((rows) => {
      if (!controller.signal.aborted) setKeys(rows);
    }).catch((err: unknown) => {
      if (!controller.signal.aborted) showError(err);
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    api.providerSpend(controller.signal).then((rows) => {
      if (!controller.signal.aborted) setSpend(rows);
    }).catch((err: unknown) => {
      if (!controller.signal.aborted) showError(err);
    });
    return () => controller.abort();
  }, [isOpen]);

  const runAction = async (provider: string, action: 'save' | 'delete' | 'verify') => {
    setBusy(true);
    setError(null);
    try {
      if (action === 'save') {
        const result = await api.setProviderKey(provider, inputs[provider].trim());
        setInputs((previous) => ({ ...previous, [provider]: '' }));
        setNotices((previous) => ({ ...previous, [provider]: result.verification?.valid === false
          ? '저장됨 · 공급자 검증 실패. 키 권한을 확인하세요.' : '키가 저장되었습니다.' }));
      } else if (action === 'delete') {
        await api.deleteProviderKey(provider);
        setInputs((previous) => ({ ...previous, [provider]: '' }));
        setNotices((previous) => ({ ...previous, [provider]: '저장된 키가 삭제되었습니다.' }));
      } else {
        const result = await api.verifyProviderKey(provider);
        setNotices((previous) => ({ ...previous, [provider]: result.valid
          ? '공급자 검증 성공' : '공급자 검증 실패. 키 권한을 확인하세요.' }));
      }
      onKeysChanged();
      setKeys(await api.listApiKeys());
      // /keys/spend uses the provider service's 15-minute cache.
      setSpend(await api.providerSpend());
    } catch (err) {
      showError(err);
    } finally {
      setBusy(false);
    }
  };

  if (!isOpen) return null;

  return <div className="modal-overlay" onClick={onClose}>
    <div className="modal-content provider-key-modal" role="dialog" aria-modal="true"
      aria-labelledby="provider-key-title" ref={dialogRef} tabIndex={-1} onClick={(event) => event.stopPropagation()}>
      <div className="modal-header">
        <div>
          <h3 id="provider-key-title"><KeyRound size={18} /> 공급자 API 키 관리</h3>
          <p className="metric-note">API 연결과 공급자가 보고한 사용액·잔액을 확인합니다.</p>
        </div>
        <button className="icon-button" aria-label="닫기" onClick={onClose}><X size={20} /></button>
      </div>
      <p className="empty-hint">키는 백엔드 PC에 암호화 저장됩니다. 등록·검증 및 청구액 조회 시 해당 공급자 공식 API로 전송됩니다.
        서버 접속용 API 키와는 별개입니다. 관리자 키는 OpenAI·Anthropic 공식 청구액 조회에 사용됩니다.</p>
      <ConnectionBanner error={error} onOpenSettings={onOpenSettings} />
      {loading ? <p className="empty-hint" role="status">키 정보를 불러오는 중…</p> : keys.map((item) => {
        const billingKey = `${item.provider.replace(/_admin$/, '')}_api`;
        const bill = spend[billingKey];
        return <section className="provider-key-row" key={item.provider} aria-label={item.name}>
          <div className="section-heading">
            <h4>{item.name}</h4>
            <span className="metric-note">{item.has_key ? item.source === 'env' ? '환경 변수 연결' : '저장된 키' : '미설정'}
              {item.is_admin_key && ' · 관리자'}</span>
          </div>
          {item.has_key && <p className="provider-key-mask">{/[*•…]/.test(item.masked_key) ? item.masked_key : '••••••••'}</p>}
          <p className="metric-note">공식 청구/잔액: {formatProviderSpend(bill)}</p>
          {bill?.as_of && <p className="metric-note">{bill.source_label} · {formatDateTime(bill.as_of)} 기준 (15분 캐시)</p>}
          <form className="rule-form provider-key-form" onSubmit={(event) => {
            event.preventDefault();
            if (!busy && inputs[item.provider]?.trim()) void runAction(item.provider, 'save');
          }}>
            <label className="rule-form-full">
              <span>{item.name} API 키 {item.has_key ? '교체' : '등록'}</span>
              <input type="password" autoComplete="off" spellCheck={false} disabled={busy}
                value={inputs[item.provider] ?? ''} placeholder={item.placeholder}
                onChange={(event) => setInputs((previous) => ({ ...previous, [item.provider]: event.target.value }))} />
            </label>
            <div className="provider-key-actions">
              <button className="btn btn-primary" disabled={busy || !inputs[item.provider]?.trim()} type="submit">저장</button>
              {item.has_key && <button className="btn btn-secondary" type="button" disabled={busy}
                onClick={() => void runAction(item.provider, 'verify')}><RefreshCw size={14} /> 검증</button>}
              {item.source === 'stored' && <button className="btn btn-secondary" type="button" disabled={busy}
                onClick={() => void runAction(item.provider, 'delete')}><Trash2 size={14} /> 삭제</button>}
            </div>
          </form>
          {notices[item.provider] && <p className="metric-note" role="status">{notices[item.provider]}</p>}
        </section>;
      })}
    </div>
  </div>;
};
