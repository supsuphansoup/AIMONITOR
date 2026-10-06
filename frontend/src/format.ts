/** Shared display formatting, so "계산 불가" means the same thing everywhere. */
import { CostSource, ProviderSpend } from './types';

/** Shown wherever a cost could not be computed, per the project rule that a
 *  missing rate is never rendered as 0원. */
export const NOT_COMPUTABLE = '계산 불가';

export function formatCost(value: number, source: CostSource, digits = 5): string {
  if (source === 'unknown') return NOT_COMPUTABLE;
  return `$${value.toFixed(digits)}`;
}

export function formatUsd(value: number, digits = 4): string {
  return `$${value.toFixed(digits)}`;
}

/** Official money keeps its reported currency; no assumed exchange rate. */
export function formatProviderSpend(spend?: ProviderSpend | null): string {
  if (!spend || spend.kind === 'unavailable' || spend.amount === null || !Number.isFinite(spend.amount)) {
    return '확인 불가';
  }
  const currency = spend.currency?.toUpperCase();
  if (!currency) return '확인 불가';
  const amount = currency === 'USD' ? formatUsd(spend.amount, 2) : `${formatNumber(spend.amount)} ${currency}`;
  return `${spend.kind === 'balance' ? '잔액' : '사용액'} ${amount}`;
}

export function formatNumber(value: number | undefined | null): string {
  return (value ?? 0).toLocaleString();
}

export function formatMs(value: number | undefined | null): string {
  const ms = value ?? 0;
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)} s`;
  return `${ms.toFixed(ms < 10 ? 1 : 0)} ms`;
}

/** Backend timestamps carry a UTC offset, so this renders in the viewer locale. */
export function formatTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '-' : date.toLocaleTimeString();
}

export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '-' : date.toLocaleString();
}

export function formatRelative(iso?: string | null): string {
  if (!iso) return '-';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '-';
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 60) return `${seconds}초 전`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}분 전`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}시간 전`;
  return `${Math.floor(seconds / 86400)}일 전`;
}
