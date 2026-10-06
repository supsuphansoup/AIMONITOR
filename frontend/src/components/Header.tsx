import React from 'react';
import { Activity, Bell, KeyRound, Play, RefreshCw, Settings } from 'lucide-react';
import { SystemInfo, TimeWindow } from '../types';
import { formatTime } from '../format';

interface HeaderProps {
  onRefresh: () => void;
  onSimulate: () => void;
  onOpenAlerts: () => void;
  onOpenSettings: () => void;
  onOpenKeys?: () => void;
  alertCount: number;
  activeRuleCount: number;
  isLoading: boolean;
  isSimulating: boolean;
  isConnected: boolean;
  lastUpdated: Date | null;
  version?: string;
  windows: TimeWindow[];
  windowIndex: number;
  onWindowChange: (index: number) => void;
  showDemoTraffic: boolean;
}

/**
 * Demo traffic is written through the real ingest API, so in production it
 * would land in real totals, alerts and exports. Hidden there, and hidden
 * while the environment is unknown (auth failure, older server).
 */
export function canGenerateDemoTraffic(info: SystemInfo | null): boolean {
  return info !== null && info.environment.trim().toLowerCase() !== 'production';
}

export const Header: React.FC<HeaderProps> = ({
  onRefresh,
  onSimulate,
  onOpenAlerts,
  onOpenSettings,
  onOpenKeys,
  alertCount,
  activeRuleCount,
  isLoading,
  isSimulating,
  isConnected,
  lastUpdated,
  version,
  windows,
  windowIndex,
  onWindowChange,
  showDemoTraffic,
}) => {
  return (
    <header className="header">
      <div className="header-brand">
        <div className="brand-logo">
          <Activity size={18} />
        </div>
        <div>
          <span className="brand-title">AIMONITOR</span>
        </div>
        <span className="brand-badge">{version ? `v${version}` : 'OBSERVABILITY ENGINE'}</span>
        <span className="header-connection" title={isConnected ? '백엔드 연결됨' : '백엔드 연결 끊김'}>
          <span className={`status-dot ${isConnected ? 'status-dot-ok' : 'status-dot-down'}`} />
          <span className="connection-label">{isConnected ? 'Connected' : 'Disconnected'}</span>
        </span>
        {lastUpdated && isConnected && (
          <span className="header-meta">최근 갱신 {formatTime(lastUpdated.toISOString())}</span>
        )}
      </div>

      <div className="header-actions">
        <div className="window-switch" role="group" aria-label="조회 기간">
          {windows.map((w, index) => (
            <button
              key={w.label}
              className={`window-option ${index === windowIndex ? 'window-option-active' : ''}`}
              aria-pressed={index === windowIndex}
              onClick={() => onWindowChange(index)}
            >
              {w.label}
            </button>
          ))}
        </div>

        <button className="btn btn-secondary btn-badge-host" onClick={onOpenAlerts}>
          <Bell size={14} />
          Alerts
          {activeRuleCount > 0 && <span className="btn-subcount">{activeRuleCount}</span>}
          {alertCount > 0 && <span className="btn-badge">{alertCount}</span>}
        </button>

        {showDemoTraffic && (
          <button className="btn btn-secondary" onClick={onSimulate} disabled={isSimulating}>
            <Play size={14} />
            {isSimulating ? 'Simulating Traffic...' : 'Generate Demo Traffic'}
          </button>
        )}

        {onOpenKeys && <button className="btn btn-secondary" onClick={onOpenKeys}>
          <KeyRound size={14} /> API 키 관리
        </button>}

        <button className="btn btn-secondary" onClick={onOpenSettings} title="설정">
          <Settings size={14} />
        </button>

        <button className="btn btn-primary" onClick={onRefresh} disabled={isLoading}>
          <RefreshCw size={14} className={isLoading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>
    </header>
  );
};
