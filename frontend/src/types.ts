export type TraceStatus = 'SUCCESS' | 'ERROR' | 'TIMEOUT' | 'RATE_LIMITED';

/** How `cost_usd` was derived. `unknown` means no rate card matched the model. */
export type CostSource = 'custom' | 'pricing_table' | 'local_free' | 'unknown';

export interface TraceEvent {
  id: number;
  trace_id: string;
  session_id?: string;
  timestamp: string;
  provider: string;
  model_name: string;
  prompt_tokens: number;
  completion_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  total_tokens: number;
  latency_ms: number;
  cost_usd: number;
  cost_source: CostSource;
  status: TraceStatus | string;
  error_message?: string;
  tags?: string;
  metadata_json?: string;
}

export interface OverviewMetrics {
  total_requests: number;
  success_count: number;
  error_count: number;
  error_rate_pct: number;
  avg_latency_ms: number;
  p50_latency_ms: number;
  p95_latency_ms: number;
  p99_latency_ms: number;
  total_cost_usd: number;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  /** Calls whose model had no rate card; their cost is not included above. */
  cost_unknown_count: number;
  /** True when percentiles were computed from a truncated sample. */
  latency_sampled: boolean;
}

export interface TimeseriesPoint {
  timestamp: string;
  request_count: number;
  error_count: number;
  avg_latency_ms: number;
  p95_latency_ms: number;
  total_cost_usd: number;
  total_tokens: number;
  /** True when the row cap cut into this bucket, so its counts are a floor. */
  partial: boolean;
}

export interface ModelBreakdown {
  provider: string;
  model_name: string;
  count: number;
  total_tokens: number;
  total_cost_usd: number;
  error_count: number;
  avg_latency_ms: number;
  cost_unknown_count: number;
}

export interface AlertRule {
  id: number;
  name: string;
  metric_type: string;
  operator: string;
  threshold_value: number;
  window_minutes: number;
  cooldown_minutes: number;
  webhook_url?: string;
  is_active: boolean;
  created_at: string;
  last_triggered_at?: string | null;
}

export interface AlertHistory {
  id: number;
  rule_id: number;
  rule_name: string;
  triggered_value: number;
  message: string;
  sent_successfully: boolean;
  created_at: string;
}

export interface AlertMetricOption {
  key: string;
  resolves_to: string;
  unit: string;
}

export interface AlertMetricCatalog {
  metrics: AlertMetricOption[];
  canonical_metrics: string[];
  operators: string[];
}

export interface SystemInfo {
  app: string;
  version: string;
  environment: string;
  auth_enabled: boolean;
  auth_required_for_reads: boolean;
  retention_days: number;
  max_batch_size: number;
  priced_models: number;
  scheduler: {
    enabled: boolean;
    running: boolean;
    jobs: string[];
    alert_interval_seconds: number;
    retention_days: number;
    last_error: string | null;
  };
}

/** Window options for the dashboard time-range selector. */
export interface TimeWindow {
  label: string;
  hours: number;
  intervalMinutes: number;
}

export const TIME_WINDOWS: TimeWindow[] = [
  { label: '1H', hours: 1, intervalMinutes: 1 },
  { label: '6H', hours: 6, intervalMinutes: 5 },
  { label: '24H', hours: 24, intervalMinutes: 15 },
  { label: '7D', hours: 168, intervalMinutes: 60 },
  { label: '30D', hours: 720, intervalMinutes: 360 },
];
