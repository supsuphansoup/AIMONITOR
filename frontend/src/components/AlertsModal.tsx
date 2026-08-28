import React, { useEffect, useState } from 'react';
import { Bell, Pause, Play, Plus, ShieldAlert, Trash2, X } from 'lucide-react';
import { api, ApiError } from '../api';
import { AlertHistory, AlertMetricOption, AlertRule } from '../types';
import { formatRelative, formatTime } from '../format';

interface AlertsModalProps {
  isOpen: boolean;
  onClose: () => void;
  rules: AlertRule[];
  histories: AlertHistory[];
  onRefresh: () => void;
}

const FALLBACK_METRICS: AlertMetricOption[] = [
  { key: 'error_rate', resolves_to: 'error_rate_pct', unit: '%' },
  { key: 'latency_p95', resolves_to: 'p95_latency_ms', unit: 'ms' },
  { key: 'total_cost', resolves_to: 'total_cost_usd', unit: 'USD' },
];

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
  const [cooldownMin, setCooldownMin] = useState('15');
  const [webhookUrl, setWebhookUrl] = useState('');
  const [isEvaluating, setIsEvaluating] = useState(false);
  const [busyRuleId, setBusyRuleId] = useState<number | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [metricOptions, setMetricOptions] = useState<AlertMetricOption[]>(FALLBACK_METRICS);

  // The catalog is served by the backend so the form can never offer a metric
  // the evaluator would reject.
  useEffect(() => {
    if (!isOpen) return;
    api
      .alertMetrics()
      .then((catalog) => setMetricOptions(catalog.metrics))
      .catch(() => setMetricOptions(FALLBACK_METRICS));
  }, [isOpen]);

  if (!isOpen) return null;

  const describeError = (err: unknown, fallback: string) =>
    err instanceof ApiError ? err.message : fallback;

  const handleCreateRule = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setFormError(null);
    try {
      await api.createRule({
        name: name.trim(),
        metric_type: metricType,
        operator,
        threshold_value: parseFloat(threshold) || 0,
        window_minutes: parseInt(windowMin, 10) || 5,
        cooldown_minutes: parseInt(cooldownMin, 10) || 0,
        webhook_url: webhookUrl.trim() || undefined,
        is_active: true,
      });
      setShowAddForm(false);
      setName('');
      setWebhookUrl('');
      onRefresh();
    } catch (err) {
      setFormError(describeError(err, '규칙을 생성하지 못했습니다.'));
    }
  };

  const handleEvaluateNow = async () => {
    setIsEvaluating(true);
    setFormError(null);
    try {
      // Bypasses the cooldown so the button does something during an ongoing
      // incident. Webhooks do fire, but the cooldown itself is left alone, so
      // the scheduled passes keep their existing rhythm.
      await api.evaluateRules(true);
      onRefresh();
    } catch (err) {
      setFormError(describeError(err, '평가에 실패했습니다.'));
    } finally {
      setIsEvaluating(false);
    }
  };

  const handleToggleRule = async (rule: AlertRule) => {
    setBusyRuleId(rule.id);
    try {
      await api.updateRule(rule.id, { is_active: !rule.is_active });
      onRefresh();
    } catch (err) {
      setFormError(describeError(err, '규칙 상태를 변경하지 못했습니다.'));
    } finally {
      setBusyRuleId(null);
    }
  };

  const handleDeleteRule = async (ruleId: number) => {
    setBusyRuleId(ruleId);
    try {
      await api.deleteRule(ruleId);
      onRefresh();
    } catch (err) {
      setFormError(describeError(err, '규칙을 삭제하지 못했습니다.'));
    } finally {
      setBusyRuleId(null);
    }
  };

  const unitFor = (key: string) => metricOptions.find((m) => m.key === key)?.unit ?? '';

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" style={{ maxWidth: '820px' }} onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <Bell size={20} color="#f59e0b" />
            <h3 style={{ fontSize: '18px', fontWeight: 600 }}>Alerts &amp; Anomaly Detection</h3>
          </div>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
            <button
              className="btn btn-secondary"
              onClick={handleEvaluateNow}
              disabled={isEvaluating}
              style={{ fontSize: '12px', padding: '6px 12px' }}
              title="쿨다운을 무시하고 지금 평가합니다. Webhook 도 전송되며, 쿨다운 자체는 변경되지 않습니다."
            >
              <Play size={12} color="#10b981" />
              {isEvaluating ? 'Evaluating...' : 'Evaluate Now'}
            </button>
            <button className="icon-button" onClick={onClose} aria-label="닫기">
              <X size={20} />
            </button>
          </div>
        </div>

        {formError && <div className="banner banner-error">{formError}</div>}

        <div>
          <div className="section-heading">
            <h4>Alert Rules ({rules.length})</h4>
            <button
              className="btn btn-primary"
              onClick={() => setShowAddForm(!showAddForm)}
              style={{ fontSize: '12px', padding: '4px 10px' }}
            >
              <Plus size={12} /> {showAddForm ? 'Cancel' : 'New Rule'}
            </button>
          </div>

          {showAddForm && (
            <form onSubmit={handleCreateRule} className="rule-form">
              <div className="rule-form-grid">
                <label>
                  <span>Rule Name</span>
                  <input
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="e.g. High Error Rate"
                    required
                  />
                </label>
                <label>
                  <span>Metric</span>
                  <select value={metricType} onChange={(e) => setMetricType(e.target.value)}>
                    {metricOptions.map((m) => (
                      <option key={m.key} value={m.key}>
                        {m.key}
                        {m.unit ? ` (${m.unit})` : ''}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Condition</span>
                  <select value={operator} onChange={(e) => setOperator(e.target.value)}>
                    <option value=">">&gt;</option>
                    <option value=">=">&gt;=</option>
                    <option value="<">&lt;</option>
                    <option value="<=">&lt;=</option>
                  </select>
                </label>
                <label>
                  <span>Threshold {unitFor(metricType) && `(${unitFor(metricType)})`}</span>
                  <input
                    type="number"
                    step="any"
                    value={threshold}
                    onChange={(e) => setThreshold(e.target.value)}
                    required
                  />
                </label>
                <label>
                  <span>Window (min)</span>
                  <input
                    type="number"
                    min="1"
                    max="1440"
                    value={windowMin}
                    onChange={(e) => setWindowMin(e.target.value)}
                    required
                  />
                </label>
                <label>
                  <span title="같은 규칙이 다시 알림을 보내기까지의 최소 간격">Cooldown (min)</span>
                  <input
                    type="number"
                    min="0"
                    max="10080"
                    value={cooldownMin}
                    onChange={(e) => setCooldownMin(e.target.value)}
                    required
                  />
                </label>
              </div>

              <label className="rule-form-full">
                <span>Webhook URL (Slack / Discord, optional)</span>
                <input
                  type="url"
                  value={webhookUrl}
                  onChange={(e) => setWebhookUrl(e.target.value)}
                  placeholder="https://hooks.slack.com/services/..."
                />
              </label>

              <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <button type="submit" className="btn btn-primary" style={{ fontSize: '12px' }}>
                  Save Rule
                </button>
              </div>
            </form>
          )}

          {rules.length === 0 ? (
            <div className="empty-hint">
              No alert rules defined. Create one above to monitor thresholds.
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {rules.map((rule) => (
                <div key={rule.id} className="rule-row">
                  <div>
                    <strong>{rule.name}</strong>
                    <span className="rule-expr">
                      {rule.metric_type} {rule.operator} {rule.threshold_value}
                      {unitFor(rule.metric_type) && ` ${unitFor(rule.metric_type)}`}
                    </span>
                    <div className="rule-meta">
                      창 {rule.window_minutes}분 · 쿨다운 {rule.cooldown_minutes}분 · 최근 발동{' '}
                      {formatRelative(rule.last_triggered_at)}
                      {rule.webhook_url ? ' · webhook 설정됨' : ''}
                    </div>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <span className={rule.is_active ? 'badge badge-success' : 'badge badge-warning'}>
                      {rule.is_active ? 'Active' : 'Paused'}
                    </span>
                    <button
                      className="icon-button"
                      onClick={() => handleToggleRule(rule)}
                      disabled={busyRuleId === rule.id}
                      title={rule.is_active ? '일시 중지' : '재개'}
                    >
                      {rule.is_active ? <Pause size={14} /> : <Play size={14} color="#10b981" />}
                    </button>
                    <button
                      className="icon-button"
                      onClick={() => handleDeleteRule(rule.id)}
                      disabled={busyRuleId === rule.id}
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

        <div>
          <div className="section-heading">
            <h4>Triggered Alert History ({histories.length})</h4>
          </div>
          <div className="table-container" style={{ maxHeight: '220px', overflowY: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Delivery</th>
                  <th>Rule Name</th>
                  <th>Value</th>
                  <th>Message</th>
                  <th>Triggered</th>
                </tr>
              </thead>
              <tbody>
                {histories.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="table-empty">
                      No alerts triggered yet. System operating normally.
                    </td>
                  </tr>
                ) : (
                  histories.map((hist) => (
                    <tr key={hist.id}>
                      <td>
                        {/* A failed webhook still records the alert; the two are
                            tracked separately so a broken hook is visible. */}
                        <span
                          className={`badge ${hist.sent_successfully ? 'badge-error' : 'badge-warning'}`}
                          title={
                            hist.sent_successfully
                              ? '알림 발송 완료'
                              : 'Webhook 전송 실패 (알림은 기록됨)'
                          }
                        >
                          <ShieldAlert size={10} style={{ marginRight: '4px' }} />
                          {hist.sent_successfully ? 'TRIGGERED' : 'SEND FAILED'}
                        </span>
                      </td>
                      <td>
                        <strong>{hist.rule_name}</strong>
                      </td>
                      <td>{hist.triggered_value}</td>
                      <td style={{ color: 'var(--text-muted)' }}>{hist.message}</td>
                      <td>{formatTime(hist.created_at)}</td>
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
