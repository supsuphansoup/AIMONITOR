import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ConnectedAIItem, ConnectedAISummary } from '../types';
import { ConnectedAICards } from './ConnectedAICards';

const usage = { requests: 1, tokens: 100, cost_usd: null, cost_unknown: 1 };
const item: ConnectedAIItem = {
  key: 'copilot', display_name: 'GitHub Copilot', provider: 'openai', plan_name: 'Copilot Pro',
  cost_meaning: 'api_equivalent', context: null, last_request_at: null, session_status: '기록 없음',
  billing_note: 'API 환산 · 실제 청구 아님', official_spend: null,
  usage: { '5h': usage, today: usage, '7d': usage, month: usage },
  quota_windows: [{ id: 'month', label: '월간', remaining_pct: null, source: 'unavailable',
    source_label: '공식 값 없음', as_of: null, resets_at: null, stale: false, note: '' }],
};

function summary(ai = item): ConnectedAISummary {
  return { items: [ai], total: { connected_count: 1, hidden_count: 0, billed_month_usd: 0,
    billed_unknown_month: 1, api_equivalent_month_usd: 0 } };
}

function renderCards(data: ConnectedAISummary | null = summary(), error?: { message: string; status: number }) {
  const props = { summary: data, error, onOpenKeys: vi.fn(), onRetry: vi.fn(), onOpenSettings: vi.fn() };
  render(<ConnectedAICards {...props} />);
  return props;
}

describe('connected AI cards', () => {
  it('does not turn unknown costs, quota or context into zero', () => {
    renderCards();
    expect(screen.getAllByText('계산 불가')).toHaveLength(1);
    expect(screen.getAllByText(/1회 · 계산 불가/)).toHaveLength(2);
    expect(screen.getAllByText('확인 불가').length).toBeGreaterThan(0);
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain('$0');
    expect(document.body.textContent).not.toContain('0% 남음');
  });

  it('preserves genuine zero and distinguishes official balance from local cost', () => {
    const zero = { ...usage, cost_usd: 0, cost_unknown: 0 };
    renderCards(summary({ ...item, usage: { '5h': zero, today: zero, '7d': zero, month: zero },
      quota_windows: [{ ...item.quota_windows[0], remaining_pct: 0, source: 'official' }],
      official_spend: { provider: 'openai', kind: 'balance', amount: 0, currency: 'USD',
        source_label: '공식 잔액', as_of: null, period_start: null },
    }));
    expect(screen.getByText('잔액 $0.00')).toBeInTheDocument();
    expect(screen.getByText('0% 남음')).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toHaveAttribute('value', '0');
    expect(screen.getAllByText(/API 환산/).length).toBeGreaterThan(0);
  });

  it('shows known partial cost alongside unpriced requests', () => {
    const partial = { ...usage, requests: 2, cost_usd: 1.5 };
    renderCards(summary({ ...item, usage: { '5h': partial, today: partial, '7d': partial, month: partial } }));
    expect(screen.getAllByText('2회 · $1.5000 (API 환산) · 1건 계산 불가')).toHaveLength(2);
  });

  it('distinguishes loading, no connected tools and failed discovery', async () => {
    const props = renderCards(null, { message: 'failure', status: 403 });
    expect(screen.getByRole('alert')).toHaveTextContent('확인할 수 없습니다');
    await userEvent.setup().click(screen.getByRole('button', { name: 'API 키 입력' }));
    expect(props.onOpenSettings).toHaveBeenCalledOnce();
    expect(screen.queryByText(/연결된 AI가 없습니다/)).not.toBeInTheDocument();
  });

  it('shows an empty state and opens provider key management', async () => {
    const props = renderCards({ ...summary(), items: [] });
    expect(screen.getByText(/연결된 AI가 없습니다/)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole('button', { name: '공급자 키 관리' }));
    expect(props.onOpenKeys).toHaveBeenCalledOnce();
  });
});
