import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { TraceTable } from './TraceTable';
import { NOT_COMPUTABLE } from '../format';
import { TraceEvent } from '../types';
import { api } from '../api';

function trace(overrides: Partial<TraceEvent> = {}): TraceEvent {
  return {
    id: 1,
    trace_id: 'trace-0001',
    timestamp: '2026-08-28T06:59:14+00:00',
    provider: 'openai',
    model_name: 'gpt-4o',
    prompt_tokens: 100,
    completion_tokens: 50,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    total_tokens: 150,
    latency_ms: 200,
    cost_usd: 0.00125,
    cost_source: 'pricing_table',
    status: 'SUCCESS',
    ...overrides,
  };
}

function renderTable(props: Partial<React.ComponentProps<typeof TraceTable>> = {}) {
  const defaults = {
    traces: [trace()],
    total: 1,
    selectedProvider: '',
    selectedStatus: '',
    page: 0,
    pageSize: 50,
    onProviderChange: vi.fn(),
    onStatusChange: vi.fn(),
    onPageChange: vi.fn(),
    onSelectTrace: vi.fn(),
    showDemoTraffic: true,
  };
  return { ...render(<TraceTable {...defaults} {...props} />), props: { ...defaults, ...props } };
}

describe('cost rendering', () => {
  it('shows an amount for a priced call', () => {
    renderTable();
    expect(screen.getByText('$0.00125')).toBeInTheDocument();
  });

  it('shows 계산 불가 for an unpriced model instead of $0', () => {
    renderTable({ traces: [trace({ cost_source: 'unknown', cost_usd: 0 })] });
    expect(screen.getByText(NOT_COMPUTABLE)).toBeInTheDocument();
    expect(screen.queryByText('$0.00000')).not.toBeInTheDocument();
  });

  it('shows $0 for local inference, which really is free', () => {
    renderTable({ traces: [trace({ cost_source: 'local_free', cost_usd: 0 })] });
    expect(screen.getByText('$0.00000')).toBeInTheDocument();
    expect(screen.queryByText(NOT_COMPUTABLE)).not.toBeInTheDocument();
  });
});

describe('cache tokens', () => {
  it('notes cached tokens on the row when present', () => {
    renderTable({ traces: [trace({ cache_read_tokens: 4000 })] });
    expect(screen.getByText(/cache/)).toBeInTheDocument();
  });

  it('stays quiet when no cache was used', () => {
    renderTable();
    expect(screen.queryByText(/cache/)).not.toBeInTheDocument();
  });
});

describe('pagination', () => {
  it('reports the real total from the server, not the page size', () => {
    renderTable({ traces: [trace()], total: 137, page: 0, pageSize: 50 });
    expect(screen.getByText(/137건/)).toBeInTheDocument();
    expect(screen.getByText(/1\/3 페이지/)).toBeInTheDocument();
  });

  it('disables previous on the first page', () => {
    renderTable({ total: 137 });
    expect(screen.getByRole('button', { name: /이전/ })).toBeDisabled();
  });

  it('disables next on the last page', () => {
    renderTable({ traces: [trace()], total: 60, page: 1, pageSize: 50 });
    expect(screen.getByRole('button', { name: /다음/ })).toBeDisabled();
  });

  it('enables next while pages remain', () => {
    renderTable({ traces: [trace()], total: 137, page: 0, pageSize: 50 });
    expect(screen.getByRole('button', { name: /다음/ })).toBeEnabled();
  });

  it('advances the page on click', () => {
    const onPageChange = vi.fn();
    renderTable({ total: 137, onPageChange });
    fireEvent.click(screen.getByRole('button', { name: /다음/ }));
    expect(onPageChange).toHaveBeenCalledWith(1);
  });
});

describe('filters', () => {
  it('reports a provider change', () => {
    const onProviderChange = vi.fn();
    renderTable({ onProviderChange });
    fireEvent.change(screen.getByLabelText('Provider filter'), { target: { value: 'anthropic' } });
    expect(onProviderChange).toHaveBeenCalledWith('anthropic');
  });

  it('offers every status the backend accepts', () => {
    renderTable();
    const select = screen.getByLabelText('Status filter');
    const options = within(select).getAllByRole('option').map((o) => o.getAttribute('value'));
    expect(options).toEqual(['', 'SUCCESS', 'ERROR', 'TIMEOUT', 'RATE_LIMITED']);
  });
});

describe('empty state', () => {
  it('invites demo traffic on the first page', () => {
    renderTable({ traces: [], total: 0 });
    expect(screen.getByText(/No traces recorded yet/)).toBeInTheDocument();
    expect(screen.getByText(/Generate Demo Traffic/)).toBeInTheDocument();
  });

  it('guides SDK integration when demo traffic is unavailable', () => {
    renderTable({ traces: [], total: 0, showDemoTraffic: false });
    expect(screen.getByText('No traces recorded yet. Connect the SDK or wait for incoming traffic.')).toBeInTheDocument();
    expect(screen.queryByText(/Generate Demo Traffic/)).not.toBeInTheDocument();
  });

  it('disables export when there is nothing to export', () => {
    renderTable({ traces: [], total: 0 });
    expect(screen.getByRole('button', { name: /CSV/ })).toBeDisabled();
  });
});

describe('row selection', () => {
  it.each(['{Enter}', ' '])('opens the same trace once from the keyboard with %s', async (key) => {
    const user = userEvent.setup();
    const onSelectTrace = vi.fn();
    const row = trace({ trace_id: 'keyboard-trace' });
    renderTable({ traces: [row], onSelectTrace });
    screen.getByRole('button', { name: '트레이스 상세 keyboard-trace' }).focus();
    await user.keyboard(key);
    expect(onSelectTrace).toHaveBeenCalledExactlyOnceWith(row);
  });

  it('passes the clicked trace up', () => {
    const onSelectTrace = vi.fn();
    const row = trace({ trace_id: 'clicked-one' });
    renderTable({ traces: [row], onSelectTrace });
    fireEvent.click(screen.getByText('gpt-4o'));
    expect(onSelectTrace).toHaveBeenCalledWith(row);
  });
});

describe('CSV export', () => {
  it('describes the provider condition when a status filter is also selected', () => {
    renderTable({ selectedProvider: 'anthropic', selectedStatus: 'ERROR' });
    expect(screen.getByRole('button', { name: 'CSV' })).toHaveAttribute('title', 'Provider 필터 기준 CSV 내보내기');
  });

  it('downloads with the provider filter and prevents duplicate exports while busy', async () => {
    const user = userEvent.setup();
    let resolve!: (value: Blob) => void;
    const exportCsv = vi.spyOn(api, 'exportCsv').mockImplementation(() => new Promise((done) => { resolve = done; }));
    const createObjectURL = vi.fn().mockReturnValue('blob:test-export');
    const revokeObjectURL = vi.fn();
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    try {
      renderTable({ selectedProvider: 'anthropic', selectedStatus: 'ERROR' });
      await user.click(screen.getByRole('button', { name: 'CSV' }));
      const busy = screen.getByRole('button', { name: '내보내는 중...' });
      expect(busy).toBeDisabled();
      await user.click(busy);
      expect(exportCsv).toHaveBeenCalledExactlyOnceWith({ provider: 'anthropic' });
      const blob = new Blob(['trace_id\ntrace-0001'], { type: 'text/csv' });
      await act(async () => resolve(blob));
      expect(createObjectURL).toHaveBeenCalledExactlyOnceWith(blob);
      expect(click).toHaveBeenCalledTimes(1);
      expect(revokeObjectURL).toHaveBeenCalledExactlyOnceWith('blob:test-export');
      expect(screen.getByRole('button', { name: 'CSV' })).toBeEnabled();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('shows an export failure and re-enables retry', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'exportCsv').mockRejectedValue(new Error('Export failed'));
    renderTable();
    await user.click(screen.getByRole('button', { name: 'CSV' }));
    expect(await screen.findByText('CSV 내보내기에 실패했습니다.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'CSV' })).toBeEnabled();
  });
});
