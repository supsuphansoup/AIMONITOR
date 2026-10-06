import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { api, ApiError, getApiKey } from './api';
import { OverviewMetrics, SystemInfo, TraceEvent } from './types';

vi.mock('./components/Charts', () => ({
  Charts: ({ showDemoTraffic }: { showDemoTraffic: boolean }) => <div data-testid="charts" data-demo={showDemoTraffic}>Charts</div>,
}));

const metrics: OverviewMetrics = {
  total_requests: 1, success_count: 1, error_count: 0, error_rate_pct: 0,
  avg_latency_ms: 200, p50_latency_ms: 200, p95_latency_ms: 200, p99_latency_ms: 200,
  total_cost_usd: 0.001, total_tokens: 150, prompt_tokens: 100, completion_tokens: 50,
  cache_read_tokens: 0, cache_write_tokens: 0, cost_unknown_count: 0, latency_sampled: false,
};
const trace: TraceEvent = {
  id: 1, trace_id: 'integration-trace', timestamp: '2026-10-06T01:00:00+00:00',
  provider: 'openai', model_name: 'gpt-4o', status: 'SUCCESS',
  prompt_tokens: 100, completion_tokens: 50, cache_read_tokens: 0, cache_write_tokens: 0,
  total_tokens: 150, latency_ms: 200, cost_usd: 0.001, cost_source: 'pricing_table',
};
const systemInfo: SystemInfo = {
  app: 'AIMONITOR', version: '0.5.0', environment: 'production', auth_enabled: false,
  auth_required_for_reads: false, retention_days: 30, max_batch_size: 100, priced_models: 10,
  scheduler: { enabled: true, running: true, jobs: [], alert_interval_seconds: 60, retention_days: 30, last_error: null },
};

beforeEach(() => {
  vi.spyOn(api, 'overview').mockResolvedValue(metrics);
  vi.spyOn(api, 'timeseries').mockResolvedValue([]);
  vi.spyOn(api, 'breakdown').mockResolvedValue([]);
  vi.spyOn(api, 'traces').mockResolvedValue({ items: [trace], total: 101 });
  vi.spyOn(api, 'alertRules').mockResolvedValue([]);
  vi.spyOn(api, 'alertHistory').mockResolvedValue([]);
  vi.spyOn(api, 'systemInfo').mockResolvedValue(systemInfo);
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
});

afterEach(() => vi.useRealTimers());

async function ready() {
  render(<App />);
  await waitFor(() => expect(screen.getByRole('button', { name: '트레이스 상세 integration-trace' })).toBeInTheDocument());
}

describe('dashboard wiring', () => {
  it.each(['production', 'unavailable', 'development'])('aligns empty-state guidance with the demo button in %s', async (environment) => {
    vi.mocked(api.traces).mockResolvedValue({ items: [], total: 0 });
    if (environment === 'unavailable') {
      vi.mocked(api.systemInfo).mockRejectedValue(new ApiError('API 키가 필요합니다.', 401));
    } else {
      vi.mocked(api.systemInfo).mockResolvedValue({ ...systemInfo, environment });
    }
    render(<App />);
    const allowDemo = environment === 'development';
    const message = allowDemo
      ? 'No traces recorded yet. Click "Generate Demo Traffic" to stream live events!'
      : 'No traces recorded yet. Connect the SDK or wait for incoming traffic.';
    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(await screen.findByTestId('charts')).toHaveAttribute('data-demo', String(allowDemo));
    if (allowDemo) {
      expect(screen.getByRole('button', { name: 'Generate Demo Traffic' })).toBeInTheDocument();
    } else {
      expect(screen.queryByRole('button', { name: 'Generate Demo Traffic' })).not.toBeInTheDocument();
    }
  });

  it('requests each window with its documented hours and bucket interval', async () => {
    const user = userEvent.setup();
    await ready();
    for (const [label, hours, interval] of [
      ['1H', 1, 1], ['6H', 6, 5], ['24H', 24, 15], ['7D', 168, 60], ['30D', 720, 360],
    ] as const) {
      await user.click(screen.getByRole('button', { name: label }));
      await waitFor(() => expect(api.overview).toHaveBeenLastCalledWith(hours, expect.any(AbortSignal)));
      expect(api.timeseries).toHaveBeenLastCalledWith(hours, interval, expect.any(AbortSignal));
      expect(api.breakdown).toHaveBeenLastCalledWith(hours, expect.any(AbortSignal));
      expect(screen.getByRole('button', { name: label })).toHaveAttribute('aria-pressed', 'true');
    }
  });

  it('resets pages for filters and retains both filters during pagination', async () => {
    const user = userEvent.setup();
    await ready();
    await user.click(screen.getByRole('button', { name: /다음/ }));
    expect(api.traces).toHaveBeenLastCalledWith(
      { provider: undefined, status: undefined, limit: 50, offset: 50 }, expect.any(AbortSignal),
    );
    await user.selectOptions(screen.getByLabelText('Provider filter'), 'anthropic');
    expect(api.traces).toHaveBeenLastCalledWith(
      { provider: 'anthropic', status: undefined, limit: 50, offset: 0 }, expect.any(AbortSignal),
    );
    await user.selectOptions(screen.getByLabelText('Status filter'), 'TIMEOUT');
    await user.click(screen.getByRole('button', { name: /다음/ }));
    expect(api.traces).toHaveBeenLastCalledWith(
      { provider: 'anthropic', status: 'TIMEOUT', limit: 50, offset: 50 }, expect.any(AbortSignal),
    );
  });

  it('polls every 5 seconds, pauses on hidden tabs and refreshes immediately on return', async () => {
    vi.useFakeTimers();
    const visibility = vi.spyOn(document, 'visibilityState', 'get');
    const view = render(<App />);
    await act(async () => {});
    expect(api.overview).toHaveBeenCalledTimes(1);
    await act(async () => { vi.advanceTimersByTime(5000); });
    expect(api.overview).toHaveBeenCalledTimes(2);
    visibility.mockReturnValue('hidden');
    fireEvent(document, new Event('visibilitychange'));
    await act(async () => { vi.advanceTimersByTime(10000); });
    expect(api.overview).toHaveBeenCalledTimes(2);
    visibility.mockReturnValue('visible');
    await act(async () => { fireEvent(document, new Event('visibilitychange')); });
    expect(api.overview).toHaveBeenCalledTimes(3);
    await act(async () => { vi.advanceTimersByTime(5000); });
    expect(api.overview).toHaveBeenCalledTimes(4);
    view.unmount();
    await act(async () => { vi.advanceTimersByTime(10000); });
    expect(api.overview).toHaveBeenCalledTimes(4);
  });

  it('opens trace details once by keyboard and returns focus on Escape', async () => {
    const user = userEvent.setup();
    await ready();
    const opener = screen.getByRole('button', { name: '트레이스 상세 integration-trace' });
    opener.focus();
    await user.keyboard('{Enter}');
    expect(screen.getAllByRole('dialog', { name: 'Trace Details' })).toHaveLength(1);
    expect(screen.getByRole('button', { name: '닫기' })).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it('opens settings from a 401 banner and retries after saving the key', async () => {
    const user = userEvent.setup();
    vi.mocked(api.overview).mockRejectedValueOnce(new ApiError('API 키가 필요합니다.', 401));
    vi.mocked(api.systemInfo).mockRejectedValueOnce(new ApiError('API 키가 필요합니다.', 401));
    render(<App />);
    await user.click(await screen.findByRole('button', { name: 'API 키 입력' }));
    await user.type(screen.getByLabelText('API 키'), 'test-key');
    await user.click(screen.getByRole('button', { name: '저장' }));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(getApiKey()).toBe('test-key');
    expect(api.overview).toHaveBeenCalledTimes(2);
  });

  it('hides demo writes in production and ingests then evaluates in development', async () => {
    const user = userEvent.setup();
    const ingest = vi.spyOn(api, 'ingestBatch').mockResolvedValue([]);
    const evaluate = vi.spyOn(api, 'evaluateRules').mockResolvedValue([]);
    const view = render(<App />);
    await waitFor(() => expect(api.systemInfo).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('button', { name: 'Generate Demo Traffic' })).not.toBeInTheDocument();
    view.unmount();
    vi.mocked(api.systemInfo).mockResolvedValue({ ...systemInfo, environment: 'development' });
    render(<App />);
    await user.click(await screen.findByRole('button', { name: 'Generate Demo Traffic' }));
    expect(ingest).toHaveBeenCalledTimes(1);
    expect(ingest.mock.calls[0][0]).toHaveLength(6);
    expect(evaluate).toHaveBeenCalledExactlyOnceWith();
    expect(ingest.mock.invocationCallOrder[0]).toBeLessThan(evaluate.mock.invocationCallOrder[0]);
  });
});
