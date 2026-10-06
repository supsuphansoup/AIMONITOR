import { describe, expect, it } from 'vitest';
import { formatCost, formatMs, formatNumber, formatProviderSpend, formatRelative, formatTime, NOT_COMPUTABLE } from './format';
import { ProviderSpend } from './types';

describe('official provider money', () => {
  const spend: ProviderSpend = { provider: 'openai', kind: 'spend', amount: 0, currency: 'USD',
    as_of: null, period_start: null, source_label: 'Official' };
  it('keeps zero distinct from absent amount, endpoint or currency', () => {
    expect(formatProviderSpend(spend)).toBe('사용액 $0.00');
    expect(formatProviderSpend({ ...spend, amount: null })).toBe('확인 불가');
    expect(formatProviderSpend({ ...spend, currency: null })).toBe('확인 불가');
    expect(formatProviderSpend({ ...spend, kind: 'unavailable' })).toBe('확인 불가');
    expect(formatProviderSpend(null)).toBe('확인 불가');
  });
  it('preserves reported non-USD currency without guessing an exchange rate', () => {
    expect(formatProviderSpend({ ...spend, currency: 'KRW', amount: 1000 })).toBe('사용액 1,000 KRW');
  });
});

describe('formatCost', () => {
  it('renders a priced call as a dollar amount', () => {
    expect(formatCost(0.0123, 'pricing_table')).toBe('$0.01230');
  });

  it('renders an unpriced call as 계산 불가, never as zero', () => {
    // The whole point: a missing rate must not read as "this call was free".
    expect(formatCost(0, 'unknown')).toBe(NOT_COMPUTABLE);
    expect(formatCost(0, 'unknown')).not.toContain('$');
  });

  it('renders genuinely free local inference as zero', () => {
    expect(formatCost(0, 'local_free')).toBe('$0.00000');
  });

  it('renders a caller-supplied cost', () => {
    expect(formatCost(1.5, 'custom', 2)).toBe('$1.50');
  });
});

describe('formatMs', () => {
  it('keeps sub-second values in milliseconds', () => {
    expect(formatMs(120)).toBe('120 ms');
    expect(formatMs(9.4)).toBe('9.4 ms');
  });

  it('promotes second-scale values', () => {
    expect(formatMs(1500)).toBe('1.50 s');
  });

  it('treats missing latency as zero rather than NaN', () => {
    expect(formatMs(undefined)).toBe('0.0 ms');
    expect(formatMs(null)).toBe('0.0 ms');
  });
});

describe('formatTime', () => {
  it('parses an offset-bearing timestamp', () => {
    // The backend always sends an offset; without one the browser would read
    // the value as local time and shift the whole dashboard.
    expect(formatTime('2026-08-28T06:59:14+00:00')).not.toBe('-');
  });

  it('degrades gracefully on garbage', () => {
    expect(formatTime('not-a-date')).toBe('-');
  });
});

describe('formatRelative', () => {
  it('describes recent instants in seconds', () => {
    const justNow = new Date(Date.now() - 5000).toISOString();
    expect(formatRelative(justNow)).toMatch(/초 전$/);
  });

  it('describes older instants in minutes and hours', () => {
    expect(formatRelative(new Date(Date.now() - 120_000).toISOString())).toBe('2분 전');
    expect(formatRelative(new Date(Date.now() - 7_200_000).toISOString())).toBe('2시간 전');
  });

  it('renders a dash when a rule has never fired', () => {
    expect(formatRelative(null)).toBe('-');
    expect(formatRelative(undefined)).toBe('-');
  });
});

describe('formatNumber', () => {
  it('groups thousands and tolerates nullish input', () => {
    expect(formatNumber(1234567)).toBe((1234567).toLocaleString());
    expect(formatNumber(undefined)).toBe('0');
  });
});
