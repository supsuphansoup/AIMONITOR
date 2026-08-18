export interface TraceEvent {
  id: number;
  trace_id: string;
  session_id?: string;
  timestamp: string;
  provider: string;
  model_name: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  latency_ms: number;
  cost_usd: number;
  status: 'SUCCESS' | 'ERROR' | 'TIMEOUT' | string;
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
}

export interface TimeseriesPoint {
  timestamp: string;
  request_count: number;
  error_count: number;
  avg_latency_ms: number;
  p95_latency_ms: number;
  total_cost_usd: number;
  total_tokens: number;
}

export interface ModelBreakdown {
  provider: string;
  model_name: string;
  count: number;
  total_tokens: number;
  total_cost_usd: number;
  error_count: number;
  avg_latency_ms: number;
}

export interface AlertRule {
  id: number;
  name: string;
  metric_type: string;
  operator: string;
  threshold_value: number;
  window_minutes: number;
  webhook_url?: string;
  is_active: boolean;
  created_at: string;
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
