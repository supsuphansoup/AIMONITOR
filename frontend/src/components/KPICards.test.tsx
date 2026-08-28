import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { KPICards } from './KPICards';
import { NOT_COMPUTABLE } from '../format';
import { OverviewMetrics } from '../types';

function metrics(overrides: Partial<OverviewMetrics> = {}): OverviewMetrics {
  return {
    total_requests: 100,
    success_count: 95,
    error_count: 5,
    error_rate_pct: 5,
    avg_latency_ms: 250,
    p50_latency_ms: 200,
    p95_latency_ms: 800,
    p99_latency_ms: 1200,
    total_cost_usd: 1.2345,
    total_tokens: 50000,
    prompt_tokens: 30000,
    completion_tokens: 20000,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    cost_unknown_count: 0,
    latency_sampled: false,
    ...overrides,
  };
}

describe('cost card', () => {
  it('shows the total when every call was priced', () => {
    render(<KPICards metrics={metrics()} windowLabel="24H" />);
    expect(screen.getByText('$1.2345')).toBeInTheDocument();
  });

  it('flags how many calls could not be priced', () => {
    render(<KPICards metrics={metrics({ cost_unknown_count: 21 })} windowLabel="24H" />);
    expect(screen.getByText(new RegExp(`21건 ${NOT_COMPUTABLE}`))).toBeInTheDocument();
  });

  it('refuses to show a total when nothing could be priced', () => {
    // Showing $0.0000 here would assert the traffic was free, which is a
    // different claim from "we have no rates for these models".
    render(
      <KPICards
        metrics={metrics({ total_requests: 10, cost_unknown_count: 10, total_cost_usd: 0 })}
        windowLabel="24H"
      />,
    );
    const values = screen.getAllByText(NOT_COMPUTABLE);
    expect(values.length).toBeGreaterThan(0);
    expect(screen.queryByText('$0.0000')).not.toBeInTheDocument();
  });
});

describe('latency card', () => {
  it('shows percentiles', () => {
    render(<KPICards metrics={metrics()} windowLabel="24H" />);
    expect(screen.getByText(/P95 800 ms/)).toBeInTheDocument();
  });

  it('marks percentiles computed from a truncated sample', () => {
    render(<KPICards metrics={metrics({ latency_sampled: true })} windowLabel="24H" />);
    expect(screen.getByText('표본 추정')).toBeInTheDocument();
  });

  it('stays unmarked when the sample was complete', () => {
    render(<KPICards metrics={metrics()} windowLabel="24H" />);
    expect(screen.queryByText('표본 추정')).not.toBeInTheDocument();
  });
});

describe('prompt cache card', () => {
  it('appears only when cache tokens were recorded', () => {
    const { rerender } = render(<KPICards metrics={metrics()} windowLabel="24H" />);
    expect(screen.queryByText('Prompt Cache')).not.toBeInTheDocument();

    rerender(<KPICards metrics={metrics({ cache_read_tokens: 8000, cache_write_tokens: 2000 })} windowLabel="24H" />);
    expect(screen.getByText('Prompt Cache')).toBeInTheDocument();
  });

  it('keeps read and write separate, because their rates differ', () => {
    render(
      <KPICards
        metrics={metrics({ cache_read_tokens: 8000, cache_write_tokens: 2000 })}
        windowLabel="24H"
      />,
    );
    expect(screen.getByText('8,000')).toBeInTheDocument();
    expect(screen.getByText(/write 2,000/)).toBeInTheDocument();
  });
});

describe('loading state', () => {
  it('renders zeros rather than crashing before the first response', () => {
    render(<KPICards metrics={null} windowLabel="24H" />);
    expect(screen.getByText('0.0%')).toBeInTheDocument();
  });
});

describe('window label', () => {
  it('says which window the totals cover', () => {
    render(<KPICards metrics={metrics()} windowLabel="7D" />);
    expect(screen.getByText(/Total Ingested Calls · 7D/)).toBeInTheDocument();
  });
});
