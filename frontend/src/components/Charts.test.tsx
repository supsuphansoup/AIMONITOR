import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Charts } from './Charts';

describe('chart descriptions', () => {
  it('names the model chart after the displayed calls and errors', () => {
    render(<Charts timeseries={[]} breakdown={[]} windowLabel="24H" showDemoTraffic={false} />);
    expect(screen.getByText('Model Calls & Errors')).toBeInTheDocument();
    expect(screen.queryByText('Model Usage & Cost')).not.toBeInTheDocument();
  });

  it('guides SDK integration when demo traffic is unavailable', () => {
    render(<Charts timeseries={[]} breakdown={[]} windowLabel="24H" showDemoTraffic={false} />);
    expect(screen.getByText('No time-series data yet. Connect the SDK or wait for incoming traffic.')).toBeInTheDocument();
    expect(screen.queryByText(/Generate demo traffic/)).not.toBeInTheDocument();
  });

  it('retains the demo invitation when the demo button is available', () => {
    render(<Charts timeseries={[]} breakdown={[]} windowLabel="24H" showDemoTraffic />);
    expect(screen.getByText('No time-series data yet. Generate demo traffic to view charts.')).toBeInTheDocument();
  });
});
