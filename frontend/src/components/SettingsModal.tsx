import React, { useEffect, useState } from 'react';
import { KeyRound, X } from 'lucide-react';
import { getApiKey, setApiKey } from '../api';
import { SystemInfo } from '../types';
import { useModalFocus } from './useModalFocus';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  systemInfo: SystemInfo | null;
  onSaved: () => void;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  systemInfo,
  onSaved,
}) => {
  const [keyValue, setKeyValue] = useState('');
  const dialogRef = useModalFocus(isOpen, onClose);

  useEffect(() => {
    if (isOpen) setKeyValue(getApiKey());
  }, [isOpen]);

  if (!isOpen) return null;

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    // Stored per browser; the server never hands the key back.
    setApiKey(keyValue);
    onSaved();
    onClose();
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-content"
        role="dialog"
        ref={dialogRef}
        tabIndex={-1}
        aria-modal="true"
        aria-labelledby="settings-title"
        style={{ maxWidth: '560px' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <KeyRound size={18} color="var(--accent-blue)" />
            <h3 id="settings-title" style={{ fontSize: 'var(--font-title)', fontWeight: 600 }}>설정</h3>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="닫기">
            <X size={20} />
          </button>
        </div>

        <form onSubmit={handleSave} className="rule-form">
          <label className="rule-form-full">
            <span>API 키</span>
            <input
              type="password"
              value={keyValue}
              onChange={(e) => setKeyValue(e.target.value)}
              placeholder={systemInfo?.auth_enabled ? '서버에 설정된 API_KEY' : '서버가 인증 없이 실행 중'}
              autoComplete="off"
            />
          </label>
          <p className="metric-note">
            브라우저에만 저장되며 모든 요청의 <code>X-API-Key</code> 헤더로 전송됩니다.
          </p>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
            <button type="button" className="btn btn-secondary" onClick={() => setKeyValue('')}>
              지우기
            </button>
            <button type="submit" className="btn btn-primary">
              저장
            </button>
          </div>
        </form>

        {systemInfo && (
          <div className="info-grid">
            <div>
              <span className="metric-label">버전</span>
              <p className="metric-value">v{systemInfo.version}</p>
            </div>
            <div>
              <span className="metric-label">인증</span>
              <p className="metric-value">{systemInfo.auth_enabled ? '활성' : '비활성'}</p>
              {!systemInfo.auth_enabled && (
                <span className="metric-note">외부 노출 전 API_KEY 를 설정하세요</span>
              )}
            </div>
            <div>
              <span className="metric-label">알림 스케줄러</span>
              <p className="metric-value">
                {systemInfo.scheduler.running
                  ? `${systemInfo.scheduler.alert_interval_seconds}초 주기`
                  : '중지됨'}
              </p>
              {systemInfo.scheduler.last_error && (
                <span className="metric-note">최근 오류: {systemInfo.scheduler.last_error}</span>
              )}
            </div>
            <div>
              <span className="metric-label">보관 기간</span>
              <p className="metric-value">
                {systemInfo.retention_days > 0 ? `${systemInfo.retention_days}일` : '무제한'}
              </p>
            </div>
            <div>
              <span className="metric-label">단가표 모델 수</span>
              <p className="metric-value">{systemInfo.priced_models}</p>
            </div>
            <div>
              <span className="metric-label">배치 최대 크기</span>
              <p className="metric-value">{systemInfo.max_batch_size}</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
