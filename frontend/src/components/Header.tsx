import React from 'react';
import { Activity, Play, RefreshCw, Bell } from 'lucide-react';

interface HeaderProps {
  onRefresh: () => void;
  onSimulate: () => void;
  onOpenAlerts: () => void;
  alertCount: number;
  isLoading: boolean;
  isSimulating: boolean;
}

export const Header: React.FC<HeaderProps> = ({
  onRefresh,
  onSimulate,
  onOpenAlerts,
  alertCount,
  isLoading,
  isSimulating,
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
        <span className="brand-badge">OBSERVABILITY ENGINE</span>
      </div>

      <div className="header-actions">
        <button
          className="btn btn-secondary"
          onClick={onOpenAlerts}
          style={{ position: 'relative' }}
        >
          <Bell size={14} color="#f59e0b" />
          Alerts
          {alertCount > 0 && (
            <span
              style={{
                position: 'absolute',
                top: '-4px',
                right: '-4px',
                background: '#ef4444',
                color: 'white',
                fontSize: '10px',
                fontWeight: 700,
                borderRadius: '9999px',
                padding: '1px 5px',
              }}
            >
              {alertCount}
            </span>
          )}
        </button>

        <button
          className="btn btn-secondary"
          onClick={onSimulate}
          disabled={isSimulating}
        >
          <Play size={14} color="#10b981" />
          {isSimulating ? 'Simulating Traffic...' : 'Generate Demo Traffic'}
        </button>

        <button
          className="btn btn-primary"
          onClick={onRefresh}
          disabled={isLoading}
        >
          <RefreshCw size={14} className={isLoading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>
    </header>
  );
};
