/**
 * Single place where the dashboard talks to the backend.
 *
 * Components used to call `fetch` inline and silently ignore failures, so a
 * backend that was down looked identical to one with no traffic. Every request
 * now goes through here: one base URL, one API key, one error shape.
 */
import {
  AlertHistory,
  AlertMetricCatalog,
  AlertRule,
  ModelBreakdown,
  OverviewMetrics,
  SystemInfo,
  TimeseriesPoint,
  TraceEvent,
} from './types';

const API_KEY_STORAGE_KEY = 'aimonitor.apiKey';

/** Defaults to the Vite dev proxy / same-origin deployment. */
const BASE_URL: string = (import.meta.env?.VITE_API_BASE ?? '/api/v1').replace(/\/$/, '');

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = 'ApiError';
  }
}

function readStoredKey(): string {
  try {
    return window.localStorage.getItem(API_KEY_STORAGE_KEY) ?? '';
  } catch {
    // Private browsing and blocked site data both throw here.
    return '';
  }
}

let apiKey: string = readStoredKey() || (import.meta.env?.VITE_API_KEY ?? '');

export function getApiKey(): string {
  return apiKey;
}

export function setApiKey(value: string): void {
  apiKey = value.trim();
  try {
    if (apiKey) {
      window.localStorage.setItem(API_KEY_STORAGE_KEY, apiKey);
    } else {
      window.localStorage.removeItem(API_KEY_STORAGE_KEY);
    }
  } catch {
    // Key still applies for this session even if it cannot be persisted.
  }
}

type QueryParams = Record<string, string | number | boolean | undefined>;

function buildUrl(path: string, params?: QueryParams): string {
  const query = new URLSearchParams();
  Object.entries(params ?? {}).forEach(([key, value]) => {
    if (value !== undefined && value !== '') query.set(key, String(value));
  });
  const suffix = query.toString();
  return `${BASE_URL}${path}${suffix ? `?${suffix}` : ''}`;
}

async function request<T>(
  path: string,
  options: { method?: string; params?: Record<string, any>; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  if (apiKey) headers['X-API-Key'] = apiKey;

  const response = await fetch(buildUrl(path, options.params), {
    method: options.method ?? 'GET',
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
  });

  if (!response.ok) {
    throw new ApiError(await describeFailure(response), response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function describeFailure(response: Response): Promise<string> {
  if (response.status === 401) return 'API 키가 필요하거나 올바르지 않습니다.';
  try {
    const body = await response.json();
    const detail = body?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg);
  } catch {
    // Fall through to the generic message.
  }
  return `요청 실패 (HTTP ${response.status})`;
}

export interface TraceQuery {
  provider?: string;
  status?: string;
  model_name?: string;
  session_id?: string;
  limit?: number;
  offset?: number;
}

/** A page of traces plus the total number of matches, from `X-Total-Count`. */
export interface TracePage {
  items: TraceEvent[];
  total: number;
}

async function requestTracePage(query: TraceQuery, signal?: AbortSignal): Promise<TracePage> {
  const headers: Record<string, string> = {};
  if (apiKey) headers['X-API-Key'] = apiKey;

  const response = await fetch(buildUrl('/traces', { ...query }), { headers, signal });
  if (!response.ok) {
    throw new ApiError(await describeFailure(response), response.status);
  }
  const items = (await response.json()) as TraceEvent[];
  const header = response.headers.get('X-Total-Count');
  // Guard on the header being present, not just numeric: Number(null) is 0, so
  // a cross-origin deployment that does not expose the header would otherwise
  // report an empty table while showing rows.
  const declared = header === null ? Number.NaN : Number(header);
  return {
    items,
    total: Number.isFinite(declared) && declared >= 0 ? declared : items.length,
  };
}

export const api = {
  overview: (hours?: number, signal?: AbortSignal) =>
    request<OverviewMetrics>('/metrics/overview', { params: { hours_back: hours }, signal }),

  timeseries: (hours: number, intervalMinutes: number, signal?: AbortSignal) =>
    request<TimeseriesPoint[]>('/metrics/timeseries', {
      params: { hours_back: hours, interval_minutes: intervalMinutes },
      signal,
    }),

  breakdown: (hours: number, signal?: AbortSignal) =>
    request<ModelBreakdown[]>('/metrics/breakdown', { params: { hours_back: hours }, signal }),

  traces: (query: TraceQuery, signal?: AbortSignal) => requestTracePage(query, signal),

  /** Fetches the CSV export as a Blob so the API key can travel in a header;
   *  a plain download link cannot carry one. */
  exportCsv: async (query: { provider?: string; limit?: number }): Promise<Blob> => {
    const headers: Record<string, string> = {};
    if (apiKey) headers['X-API-Key'] = apiKey;
    const response = await fetch(buildUrl('/traces/export.csv', query), { headers });
    if (!response.ok) {
      throw new ApiError(await describeFailure(response), response.status);
    }
    return response.blob();
  },

  alertRules: (signal?: AbortSignal) => request<AlertRule[]>('/alerts/rules', { signal }),

  alertHistory: (signal?: AbortSignal) =>
    request<AlertHistory[]>('/alerts/history', { params: { limit: 100 }, signal }),

  alertMetrics: (signal?: AbortSignal) =>
    request<AlertMetricCatalog>('/alerts/metrics', { signal }),

  systemInfo: (signal?: AbortSignal) => request<SystemInfo>('/system/info', { signal }),

  createRule: (rule: Partial<AlertRule>) =>
    request<AlertRule>('/alerts/rules', { method: 'POST', body: rule }),

  updateRule: (id: number, patch: Partial<AlertRule>) =>
    request<AlertRule>(`/alerts/rules/${id}`, { method: 'PATCH', body: patch }),

  deleteRule: (id: number) => request<void>(`/alerts/rules/${id}`, { method: 'DELETE' }),

  evaluateRules: (ignoreCooldown = false) =>
    request<AlertHistory[]>('/alerts/evaluate', {
      method: 'POST',
      params: { ignore_cooldown: ignoreCooldown },
    }),

  ingestBatch: (traces: unknown[]) =>
    request<TraceEvent[]>('/traces/batch', { method: 'POST', body: { traces } }),
};
