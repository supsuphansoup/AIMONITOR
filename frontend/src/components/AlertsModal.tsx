import React, { useState } from 'react';
import { AlertRule, AlertHistory } from '../types';
import { X, Bell, Plus, Play, ShieldAlert, Trash2 } from 'lucide-react';

interface AlertsModalProps {
  isOpen: boolean;
  onClose: () => void;
  rules: AlertRule[];
  histories: AlertHistory[];
  onRefresh: () => void;
}

export const AlertsModal: React.FC<AlertsModalProps> = ({
  isOpen,
  onClose,
  rules,
  histories,
  onRefresh,
}) => {
  const [showAddForm, setShowAddForm] = useState(false);
  const [name, setName] = useState('');
  const [metricType, setMetricType] = useState('error_rate');
  const [operator, setOperator] = useState('>');
  const [threshold, setThreshold] = useState('5.0');
  const [windowMin, setWindowMin] = useState('5');
  const [webhookUrl, setWebhookUrl] = useState('');
  const [isEvaluating, setIsEvaluating] = useState(false);

  if (!isOpen) return null;

  const handleCreateRule = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;

    try {
      const res = await fetch('/api/v1/alerts/rules', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name.trim(),
          metric_type: metricType,
          operator: operator,
          threshold_value: parseFloat(threshold) || 0,
          window_minutes: parseInt(windowMin, 10) || 5,
          webhook_url: webhookUrl.trim() || undefined,
          is_active: true,
        }),
      });
      if (res.ok) {
        setShowAddForm(false);
        setName('');
        setWebhookUrl('');
        onRefresh();
      }
    } catch (err) {
      console.error('Failed to create alert rule:', err);
    }
  };

  const handleEvaluateNow = async () => {
    setIsEvaluating(true);
    try {
      await fetch('/api/v1/alerts/evaluate', { method: 'POST' });
      onRefresh();
    } catch (err) {
      console.error('Evaluation failed:', err);
    } finally {
      setIsEvaluating(false);
    }
  };

  const handleDeleteRule = async (ruleId: number) => {
    try {
      const res = await fetch(`/api/v1/alerts/rules/${ruleId}`, { method: 'DELETE' });
      if (res.ok) {
        onRefresh();
      }
    } catch (err) {
      console.error('Failed to delete alert rule:', err);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-content"
        style={{ maxWidth: '750px' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <Bell size={20} color="#f59e0b" />
            <h3 style={{ fontSize: '18px', fontWeight: 600 }}>Alerts & Anomaly Detection</h3>
          </div>
          <div style={{ display: 'flex', gap: '8px' }}>
            <button
              className="btn btn-secondary"
              onClick={handleEvaluateNow}
              disabled={isEvaluating}
              style={{ fontSize: '12px', padding: '6px 12px' }}
            >
              <Play size={12} color="#10b981" />
              {isEvaluating ? 'Evaluating...' : 'Evaluate Rules'}
            </button>
            <button
              onClick={onClose}
              style={{ background: 'transparent', border: 'none', color: 'var(--text-muted)', cursor: 'pointer' }}
            >
              <X size={20} />
            </button>
          </div>
        </div>

        {/* Rules Section */}
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
            <h4 style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-muted)' }}>
              Active Alert Rules ({rules.length})
            </h4>
            <button
              className="btn btn-primary"
              onClick={() => setShowAddForm(!showAddForm)}
              style={{ fontSize: '12px', padding: '4px 10px' }}
            >
              <Plus size={12} /> {showAddForm ? 'Cancel' : 'New Rule'}
            </button>
          </div>

          {showAddForm && (
            <form
              onSubmit={handleCreateRule}
              style={{
                background: 'var(--bg-subtle)',
                padding: '16px',
                borderRadius: '6px',
                marginBottom: '16px',
                display: 'flex',
                flexDirection: 'column',
                gap: '12px',
              }}
            >
              <div style={{ display: 'grid', gridTemplateColumns: '2fr 1.5fr 1fr 1fr 1fr', gap: '10px' }}>
                <div>
                  <label style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Rule Name</label>
                  <input
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="e.g. High Error Rate"
                    required
                    style={{
                      width: '100%',
                      background: 'var(--bg-card)',
                      color: 'var(--text-main)',
                      border: '1px solid var(--border-color)',
                      borderRadius: '4px',
                      padding: '6px 8px',
                      fontSize: '12px',
                    }}
                  />
                </div>
                <div>
                  <label style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Metric</label>
                  <select
                    value={metricType}
                    onChange={(e) => setMetricType(e.target.value)}
                    style={{
                      width: '100%',
                      background: 'var(--bg-card)',
                      color: 'var(--text-main)',
                      border: '1px solid var(--border-color)',
                      borderRadius: '4px',
                      padding: '6px 8px',
                      fontSize: '12px',
                    }}
                  >
                    <option value="error_rate">Error Rate (%)</option>
                    <option value="latency_p95">Latency P95 (ms)</option>
                    <option value="total_cost">Total Cost ($)</option>
                  </select>
                </div>
                <div>
                  <label style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Condition</label>
                  <select
                    value={operator}
                    onChange={(e) => setOperator(e.target.value)}
                    style={{
                      width: '100%',
                      background: 'var(--bg-card)',
                      color: 'var(--text-main)',
                      border: '1px solid var(--border-color)',
                      borderRadius: '4px',
                      padding: '6px 8px',
                      fontSize: '12px',
                    }}
                  >
                    <option value=">">&gt; (Greater)</option>
                    <option value=">=">&gt;= (Greater/Eq)</option>
                    <option value="<">&lt; (Less)</option>
                  </select>
                </div>
                <div>
                  <label style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Threshold</label>
                  <input
                    type="number"
                    step="any"
                    value={threshold}
                    onChange={(e) => setThreshold(e.target.value)}
                    required
                    style={{
                      width: '100%',
                      background: 'var(--bg-card)',
                      color: 'var(--text-main)',
                      border: '1px solid var(--border-color)',
                      borderRadius: '4px',
                      padding: '6px 8px',
                      fontSize: '12px',
                    }}
                  />
                </div>
                <div>
                  <label style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Window (min)</label>
                  <input
                    type="number"
                    min="1"
                    max="1440"
                    value={windowMin}
                    onChange={(e) => setWindowMin(e.target.value)}
                    required
                    style={{
                      width: '100%',
                      background: 'var(--bg-card)',
                      color: 'var(--text-main)',
                      border: '1px solid var(--border-color)',
                      borderRadius: '4px',
                      padding: '6px 8px',
                      fontSize: '12px',
                    }}
                  />
                </div>
              </div>

              <div>
                <label style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                  Webhook URL (Slack/Discord optional)
                </label>
                <input
                  type="url"
                  value={webhookUrl}
                  onChange={(e) => setWebhookUrl(e.target.value)}
                  placeholder="https://hooks.slack.com/services/..."
                  style={{
                    width: '100%',
                    background: 'var(--bg-card)',
                    color: 'var(--text-main)',
                    border: '1px solid var(--border-color)',
                    borderRadius: '4px',
                    padding: '6px 8px',
                    fontSize: '12px',
                  }}
                />
              </div>

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
                <button type="submit" className="btn btn-primary" style={{ fontSize: '12px' }}>
                  Save Rule
                </button>
              </div>
            </form>
          )}

          {rules.length === 0 ? (
            <div style={{ color: 'var(--text-faint)', fontSize: '13px', padding: '10px 0' }}>
              No alert rules defined. Create one above to monitor thresholds.
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {rules.map((rule) => (
                <div
                  key={rule.id}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    padding: '10px 14px',
                    background: 'var(--bg-subtle)',
                    borderRadius: '6px',
                    fontSize: '13px',
                  }}
                >
                  <div>
                    <strong>{rule.name}</strong>: {rule.metric_type} {rule.operator} {rule.threshold_value} (Window: {rule.window_minutes}m)
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <span className="badge badge-success">Active</span>
                    <button
                      onClick={() => handleDeleteRule(rule.id)}
                      style={{
                        background: 'transparent',
                        border: 'none',
                        color: 'var(--text-faint)',
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                      }}
                      title="Delete Rule"
                    >
                      <Trash2 size={14} color="#ef4444" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Alert History Section */}
        <div>
          <h4 style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '10px' }}>
            Triggered Alert History ({histories.length})
          </h4>
          <div className="table-container" style={{ maxHeight: '220px', overflowY: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Status</th>
                  <th>Rule Name</th>
                  <th>Value</th>
                  <th>Message</th>
                  <th>Triggered Time</th>
                </tr>
              </thead>
              <tbody>
                {histories.length === 0 ? (
                  <tr>
                    <td colSpan={5} style={{ textAlign: 'center', color: 'var(--text-faint)', padding: '20px' }}>
                      No alerts triggered yet. System operating normally.
                    </td>
                  </tr>
                ) : (
                  histories.map((hist) => (
                    <tr key={hist.id}>
                      <td>
                        <span className="badge badge-error">
                          <ShieldAlert size={10} style={{ marginRight: '4px' }} /> TRIGGERED
                        </span>
                      </td>
                      <td><strong>{hist.rule_name}</strong></td>
                      <td>{hist.triggered_value}</td>
                      <td style={{ color: 'var(--text-muted)' }}>{hist.message}</td>
                      <td>{new Date(hist.created_at).toLocaleTimeString()}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
};
