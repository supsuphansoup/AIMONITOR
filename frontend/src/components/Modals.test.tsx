import { useState } from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api, getApiKey, setApiKey } from '../api';
import { formatDateTime } from '../format';
import { AlertHistory, AlertRule, TraceEvent } from '../types';
import { AlertsModal } from './AlertsModal';
import { SettingsModal } from './SettingsModal';
import { TraceDetailModal } from './TraceDetailModal';

const trace: TraceEvent = {
  id: 1, trace_id: 'keyboard-trace', timestamp: '2026-10-06T01:00:00+00:00',
  provider: 'anthropic', model_name: 'unknown-model', status: 'ERROR',
  prompt_tokens: 100, completion_tokens: 20, cache_read_tokens: 4000, cache_write_tokens: 1000,
  total_tokens: 5120, latency_ms: 200, cost_usd: 0, cost_source: 'unknown',
  error_message: 'Upstream timeout', metadata_json: '{"env":"test"}',
};
const rule: AlertRule = {
  id: 7, name: 'Error threshold', metric_type: 'error_rate', operator: '>', threshold_value: 5,
  window_minutes: 5, cooldown_minutes: 15, is_active: true, created_at: trace.timestamp,
};

beforeEach(() => {
  vi.spyOn(api, 'alertMetrics').mockResolvedValue({
    metrics: [{ key: 'error_rate', resolves_to: 'error_rate_pct', unit: '%' }],
    canonical_metrics: ['error_rate_pct'], operators: ['>'],
  });
});

const modalNames = { trace: 'Trace Details', alerts: 'Alerts & Anomaly Detection', settings: '설정' };
type ModalKind = keyof typeof modalNames;

function Harness({ kind, onClose }: { kind: ModalKind; onClose: () => void }) {
  const [open, setOpen] = useState(false);
  const close = () => { onClose(); setOpen(false); };
  return <>
    <button onClick={() => setOpen(true)}>Open modal</button>
    {kind === 'trace' && <TraceDetailModal trace={open ? trace : null} onClose={close} />}
    {kind === 'alerts' && <AlertsModal isOpen={open} onClose={close} rules={[]} histories={[]} onRefresh={vi.fn()} />}
    {kind === 'settings' && <SettingsModal isOpen={open} onClose={close} systemInfo={null} onSaved={vi.fn()} />}
  </>;
}

describe('modal keyboard access', () => {
  it.each<ModalKind>(['trace', 'alerts', 'settings'])('%s moves, contains and restores focus', async (kind) => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const view = render(<Harness kind={kind} onClose={onClose} />);
    const opener = screen.getByRole('button', { name: 'Open modal' });
    await user.click(opener);
    const dialog = screen.getByRole('dialog', { name: modalNames[kind] });
    const close = within(dialog).getByRole('button', { name: '닫기' });
    expect(close).toHaveFocus();
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    const buttons = within(dialog).getAllByRole('button');
    const first = buttons[0];
    const last = buttons[buttons.length - 1];
    last.focus();
    await user.tab();
    expect(first).toHaveFocus();
    await user.tab({ shift: true });
    expect(last).toHaveFocus();
    opener.focus();
    expect(dialog.contains(document.activeElement)).toBe(true);
    // Polls recreate onClose; they must not steal focus from a form control.
    const input = dialog.querySelector('input');
    const focused = input ?? close;
    focused.focus();
    view.rerender(<Harness kind={kind} onClose={onClose} />);
    expect(focused).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it('keeps inner clicks open and supports close button and backdrop clicks', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<Harness kind="trace" onClose={onClose} />);
    const open = () => user.click(screen.getByRole('button', { name: 'Open modal' }));
    await open();
    await user.click(screen.getByRole('heading', { name: 'Trace Details' }));
    expect(onClose).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: '닫기' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    await open();
    fireEvent.click(screen.getByRole('dialog').parentElement!);
    expect(onClose).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('preserves unknown cost, separate cache tokens, errors and local timestamps in trace details', () => {
    render(<TraceDetailModal trace={trace} onClose={vi.fn()} />);
    expect(screen.getByText('계산 불가')).toBeInTheDocument();
    expect(screen.queryByText('$0.000000')).not.toBeInTheDocument();
    expect(screen.getByText('Cache Read')).toBeInTheDocument();
    expect(screen.getByText('Cache Write')).toBeInTheDocument();
    expect(screen.getByText('4,000')).toBeInTheDocument();
    expect(screen.getByText('1,000')).toBeInTheDocument();
    expect(screen.getByText(/Upstream timeout/)).toBeInTheDocument();
    expect(screen.getByText(formatDateTime(trace.timestamp))).toBeInTheDocument();
  });
});

describe('settings storage', () => {
  it('loads the key, saves a replacement and refreshes before closing', async () => {
    const user = userEvent.setup();
    setApiKey('old-key');
    const onSaved = vi.fn();
    const onClose = vi.fn();
    render(<SettingsModal isOpen onClose={onClose} onSaved={onSaved} systemInfo={null} />);
    const input = screen.getByLabelText('API 키');
    expect(input).toHaveValue('old-key');
    await user.clear(input);
    await user.type(input, 'replacement-key');
    await user.click(screen.getByRole('button', { name: '저장' }));
    expect(getApiKey()).toBe('replacement-key');
    expect(window.localStorage.getItem('aimonitor.apiKey')).toBe('replacement-key');
    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('persists clearing only when saved', async () => {
    const user = userEvent.setup();
    setApiKey('old-key');
    render(<SettingsModal isOpen onClose={vi.fn()} onSaved={vi.fn()} systemInfo={null} />);
    await user.click(screen.getByRole('button', { name: '지우기' }));
    expect(screen.getByLabelText('API 키')).toHaveValue('');
    expect(getApiKey()).toBe('old-key');
    await user.click(screen.getByRole('button', { name: '저장' }));
    expect(getApiKey()).toBe('');
  });
});

describe('alert actions', () => {
  it('creates a rule and refreshes the list', async () => {
    const user = userEvent.setup();
    const create = vi.spyOn(api, 'createRule').mockResolvedValue(rule);
    const onRefresh = vi.fn();
    render(<AlertsModal isOpen onClose={vi.fn()} onRefresh={onRefresh} rules={[]} histories={[]} />);
    await user.click(screen.getByRole('button', { name: 'New Rule' }));
    await user.type(screen.getByLabelText('Rule Name'), 'Error threshold');
    await user.click(screen.getByRole('button', { name: 'Save Rule' }));
    await waitFor(() => expect(create).toHaveBeenCalledExactlyOnceWith({
      name: 'Error threshold', metric_type: 'error_rate', operator: '>', threshold_value: 5,
      window_minutes: 5, cooldown_minutes: 15, webhook_url: undefined, is_active: true,
    }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: 'Save Rule' })).not.toBeInTheDocument();
  });

  it('pauses, resumes and deletes with the original rule ID', async () => {
    const user = userEvent.setup();
    const update = vi.spyOn(api, 'updateRule').mockResolvedValue(rule);
    const remove = vi.spyOn(api, 'deleteRule').mockResolvedValue(undefined);
    const onRefresh = vi.fn();
    const props = { isOpen: true, onClose: vi.fn(), onRefresh, rules: [rule], histories: [] };
    const view = render(<AlertsModal {...props} />);
    await user.click(screen.getByTitle('일시 중지'));
    expect(update).toHaveBeenLastCalledWith(7, { is_active: false });
    view.rerender(<AlertsModal {...props} rules={[{ ...rule, is_active: false }]} />);
    await user.click(screen.getByTitle('재개'));
    expect(update).toHaveBeenLastCalledWith(7, { is_active: true });
    await user.click(screen.getByTitle('Delete Rule'));
    expect(remove).toHaveBeenCalledExactlyOnceWith(7);
    expect(onRefresh).toHaveBeenCalledTimes(3);
  });

  it('evaluates with ignore_cooldown and prevents duplicate submissions while busy', async () => {
    const user = userEvent.setup();
    let resolve!: (value: AlertHistory[]) => void;
    const evaluate = vi.spyOn(api, 'evaluateRules').mockImplementation(() => new Promise((done) => { resolve = done; }));
    const onRefresh = vi.fn();
    render(<AlertsModal isOpen onClose={vi.fn()} onRefresh={onRefresh} rules={[rule]} histories={[]} />);
    await user.click(screen.getByRole('button', { name: 'Evaluate Now' }));
    const busy = screen.getByRole('button', { name: 'Evaluating...' });
    expect(busy).toBeDisabled();
    await user.click(busy);
    expect(evaluate).toHaveBeenCalledExactlyOnceWith(true);
    await act(async () => resolve([]));
    expect(onRefresh).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Evaluate Now' })).toBeEnabled();
  });
});
