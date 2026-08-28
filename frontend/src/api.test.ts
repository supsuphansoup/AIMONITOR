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
