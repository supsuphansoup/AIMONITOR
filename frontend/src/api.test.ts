import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, getApiKey, setApiKey } from './api';

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
  setApiKey('');
});

afterEach(() => {
  setApiKey('');
  vi.unstubAllGlobals();
});

describe('api key handling', () => {
  it('uses the server key header for provider management and sends provider secrets only in the POST body', async () => {
    setApiKey('server-write-key');
    fetchMock.mockImplementation(async () => jsonResponse({}));
    await api.setProviderKey('openai', 'provider-secret');
    await api.deleteProviderKey('openai');
    await api.verifyProviderKey('openai');
    await api.listApiKeys();
    await api.providerSpend();
    await api.connectedAis();
    for (const [url, options] of fetchMock.mock.calls) {
      expect(options.headers['X-API-Key']).toBe('server-write-key');
      expect(url).not.toContain('provider-secret');
    }
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ provider: 'openai', key: 'provider-secret' });
    expect(fetchMock.mock.calls[0][1].method).toBe('POST');
    expect(fetchMock.mock.calls[1][1].method).toBe('DELETE');
    expect(fetchMock.mock.calls[2][1].method).toBe('POST');
  });

  it.each([400, 401, 403, 422, 500])('never exposes provider secret in failed key requests (%s)', async (status) => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'provider-secret' }, { status }));
    const error = await api.setProviderKey('openai', 'provider-secret').catch((err: ApiError) => err);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).not.toContain('provider-secret');
    expect((error as ApiError).status).toBe(status);
  });

  it('omits the header when no key is set', async () => {
    fetchMock.mockResolvedValue(jsonResponse([]));
    await api.alertRules();
    expect(fetchMock.mock.calls[0][1].headers['X-API-Key']).toBeUndefined();
  });

  it('sends the key on every request once set', async () => {
    setApiKey('secret-123');
    fetchMock.mockResolvedValue(jsonResponse([]));
    await api.alertRules();
    expect(fetchMock.mock.calls[0][1].headers['X-API-Key']).toBe('secret-123');
  });

  it('persists the key for the next page load', () => {
    setApiKey('  padded-key  ');
    expect(getApiKey()).toBe('padded-key');
    expect(window.localStorage.getItem('aimonitor.apiKey')).toBe('padded-key');
  });

  it('clears the stored key when set to empty', () => {
    setApiKey('temp');
    setApiKey('');
    expect(window.localStorage.getItem('aimonitor.apiKey')).toBeNull();
  });
});

describe('error translation', () => {
  it('reports 401 as a key problem, with the status attached', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'Missing or invalid API key.' }, { status: 401 }));
    // The banner keys off the status code, not the message text, because when
    // reads are gated the dashboard cannot even load /system/info.
    await expect(api.alertRules()).rejects.toMatchObject({ status: 401 });
  });

  it('surfaces a server-provided detail string', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'Batch too large' }, { status: 422 }));
    await expect(api.alertRules()).rejects.toThrow('Batch too large');
  });

  it('unwraps a pydantic validation array', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ detail: [{ msg: 'Value error, Unknown metric_type' }] }, { status: 422 }),
    );
    await expect(api.alertRules()).rejects.toThrow(/Unknown metric_type/);
  });

  it('falls back to the status code for an unreadable body', async () => {
    fetchMock.mockResolvedValue(new Response('not json', { status: 503 }));
    await expect(api.alertRules()).rejects.toThrow(/503/);
  });

  it('throws ApiError, so callers can branch on status', async () => {
    fetchMock.mockResolvedValue(jsonResponse({}, { status: 500 }));
    await expect(api.alertRules()).rejects.toBeInstanceOf(ApiError);
  });
});

describe('trace paging', () => {
  it('reads the total from X-Total-Count', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify([{ id: 1 }]), {
        status: 200,
        headers: { 'Content-Type': 'application/json', 'X-Total-Count': '137' },
      }),
    );
    const page = await api.traces({ limit: 50 });
    expect(page.total).toBe(137);
    expect(page.items).toHaveLength(1);
  });

  it('falls back to the page length when the header is not exposed', async () => {
    fetchMock.mockResolvedValue(jsonResponse([{ id: 1 }, { id: 2 }]));
    const page = await api.traces({});
    expect(page.total).toBe(2);
  });

  it('drops empty filters from the query string', async () => {
    fetchMock.mockResolvedValue(jsonResponse([]));
    await api.traces({ provider: '', status: 'ERROR', limit: 50 });
    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain('status=ERROR');
    expect(url).not.toContain('provider=');
  });
});

describe('csv export', () => {
  it('passes status, offset-bearing start time and limit in the export query', async () => {
    fetchMock.mockResolvedValue(new Response('id\n', { status: 200 }));
    await api.exportCsv({
      provider: 'anthropic', status: 'ERROR', start_time: '2026-10-06T10:00:00+09:00', limit: 25,
    });
    const url = new URL(fetchMock.mock.calls[0][0] as string, 'http://localhost');
    expect(url.pathname).toBe('/api/v1/traces/export.csv');
    expect(Object.fromEntries(url.searchParams)).toEqual({
      provider: 'anthropic', status: 'ERROR', start_time: '2026-10-06T10:00:00+09:00', limit: '25',
    });
  });

  it('omits empty provider and status filters while keeping the period', async () => {
    fetchMock.mockResolvedValue(new Response('id\n', { status: 200 }));
    await api.exportCsv({ provider: '', status: '', start_time: '2026-10-05T13:00:00+00:00' });
    const url = new URL(fetchMock.mock.calls[0][0] as string, 'http://localhost');
    expect(Object.fromEntries(url.searchParams)).toEqual({ start_time: '2026-10-05T13:00:00+00:00' });
  });

  it('returns a blob and carries the api key', async () => {
    setApiKey('secret-123');
    fetchMock.mockResolvedValue(new Response('id,trace_id\n1,abc\n', { status: 200 }));
    const blob = await api.exportCsv({ provider: 'openai' });
    expect(blob).toBeInstanceOf(Blob);
    expect(fetchMock.mock.calls[0][1].headers['X-API-Key']).toBe('secret-123');
    expect(fetchMock.mock.calls[0][0]).toContain('provider=openai');
  });

  it('raises rather than downloading an error page', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'nope' }, { status: 401 }));
    await expect(api.exportCsv({})).rejects.toBeInstanceOf(ApiError);
  });
});

describe('manual evaluation', () => {
  it('asks the server to skip cooldowns without resetting them', async () => {
    fetchMock.mockResolvedValue(jsonResponse([]));
    await api.evaluateRules(true);
    expect(fetchMock.mock.calls[0][0]).toContain('ignore_cooldown=true');
  });
});
