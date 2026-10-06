import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Header, canGenerateDemoTraffic } from './Header';
import { SystemInfo, TIME_WINDOWS } from '../types';

function info(environment: string): SystemInfo {
  return { environment } as SystemInfo;
}

function renderHeader(showDemoTraffic: boolean) {
  render(
    <Header
      onRefresh={vi.fn()}
      onSimulate={vi.fn()}
      onOpenAlerts={vi.fn()}
      onOpenSettings={vi.fn()}
      alertCount={0}
      activeRuleCount={0}
      isLoading={false}
      isSimulating={false}
      isConnected
      lastUpdated={null}
      windows={TIME_WINDOWS}
      windowIndex={0}
      onWindowChange={vi.fn()}
      showDemoTraffic={showDemoTraffic}
    />,
  );
}

describe('demo traffic button', () => {
  it('is offered outside production', () => {
    expect(canGenerateDemoTraffic(info('development'))).toBe(true);
    expect(canGenerateDemoTraffic(info('staging'))).toBe(true);
  });

  it('is hidden in production, whatever the casing', () => {
    expect(canGenerateDemoTraffic(info('production'))).toBe(false);
    expect(canGenerateDemoTraffic(info(' Production '))).toBe(false);
  });

  it('is hidden while the environment is unknown', () => {
    // Fail safe: an auth error or an older server must not expose a button
    // that writes fake traces into real data.
    expect(canGenerateDemoTraffic(null)).toBe(false);
  });

  it('renders only when allowed', () => {
    renderHeader(false);
    expect(screen.queryByText('Generate Demo Traffic')).toBeNull();
  });

  it('renders when allowed', () => {
    renderHeader(true);
    expect(screen.getByText('Generate Demo Traffic')).toBeTruthy();
  });
});
