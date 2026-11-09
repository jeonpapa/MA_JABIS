// 해외약가 모니터링 API (2026-11-10)
import { api, getToken, clearAuth, ApiError } from './client';
import type { PriceIdentity } from './priceRequests';

export type MonitorSchedule =
  | { type: 'weekly'; weekdays: number[]; hour: number }
  | { type: 'monthly'; days: number[]; hour: number }
  | { type: 'dates'; dates: string[]; hour: number };

export interface MonitorItem { id?: number; query: string; identity?: PriceIdentity | null; label?: string }
export interface MonitorRunBrief { id: string; due_at: string; start_at?: string; status: MonitorRunStatus; sent_at?: string | null }
export type MonitorRunStatus = 'scheduled' | 'collecting' | 'ready' | 'mailing' | 'sent' | 'failed' | 'cancelled';

export interface MonitorProject {
  id: string; name: string; owner: string; recipients: string[]; countries: string[]; schedule: MonitorSchedule;
  status: 'active' | 'paused'; items: MonitorItem[]; schedule_text: string; lead_hours: number;
  next_run: MonitorRunBrief | null; last_sent_run: MonitorRunBrief | null; created_at: string; updated_at: string;
}

export type MonitorKind = 'first' | 'same' | 'price' | 'basis' | 'packs' | 'now_priced' | 'lost' | 'unpriced';
export interface MonitorChange { key: string; label: string; old: number[] | null; new: number[] | null; pct: number | null; currency: string }
export interface MonitorCountryDiff {
  kind: MonitorKind; changes: MonitorChange[]; fx: { old_krw: number; new_krw: number; pct: number } | null;
  status: string; verdict?: string | null; reason?: string | null;
}
export interface MonitorDiff {
  items: { query: string; label: string; countries: Record<string, MonitorCountryDiff>; highlight: boolean; is_new_item: boolean }[];
  totals: Record<MonitorKind | 'fx', number>; has_previous: boolean; highlight_count: number; previous_due_at?: string | null;
}
export interface MonitorSnapRow {
  product_name?: string; package_unit?: string; pack_count?: number; local_price?: number; currency?: string;
  source_label?: string; source_url?: string; adjusted_price_krw?: number; searched_at?: string;
}
export interface MonitorSnapCountry {
  status: string; reason?: string | null; verdict?: string | null; verdict_sources?: { url: string; finding: string }[];
  request_id?: string; rows: MonitorSnapRow[];
}
export interface MonitorSnapshot { items: { query: string; label: string; countries: Record<string, MonitorSnapCountry> }[]; countries: string[] }

export interface MonitorRun {
  id: string; project_id: string; project_name?: string; due_at: string; start_at: string; status: MonitorRunStatus;
  items: MonitorItem[] | null; countries: string[] | null; recipients: string[] | null;
  mail_status: string; mail_via?: string | null; sent_at?: string | null; started_at?: string | null; ready_at?: string | null;
  error?: string | null; snapshot?: MonitorSnapshot | null; diff?: MonitorDiff | null;
  totals?: MonitorDiff['totals'] | null; highlight_count?: number | null; item_count?: number;
  progress?: { query: string; requests: { id: string; status: string; countries: { country: string; status: string; verdict?: string | null }[] }[] }[];
}

export interface MonitorConfig {
  domains: string[]; countries: string[]; hours_per_item_a8: number; telegram: boolean;
  verdicts: Record<string, string>; worker: { state: string; last_seen_at: string | null; open_requests: number };
}

export interface ProjectInput {
  name: string; items: MonitorItem[]; countries: string[]; schedule: MonitorSchedule; recipients: string[];
}

export const fetchMonitorConfig = () => api.get<MonitorConfig>('/api/price-monitor/config');
export const fetchMonitorProjects = () => api.get<{ projects: MonitorProject[] }>('/api/price-monitor/projects');
export const fetchMonitorProject = (id: string) => api.get<{ project: MonitorProject; runs: MonitorRun[] }>(`/api/price-monitor/projects/${id}`);
export const createMonitorProject = (p: ProjectInput) => api.post<{ project: MonitorProject }>('/api/price-monitor/projects', p);
export const updateMonitorProject = (id: string, patch: Partial<ProjectInput> & { status?: 'active' | 'paused' }) =>
  api.patch<{ project: MonitorProject }>(`/api/price-monitor/projects/${id}`, patch);
export const deleteMonitorProject = (id: string) => api.delete<{ ok: boolean }>(`/api/price-monitor/projects/${id}`);
export const runMonitorNow = (id: string) => api.post<{ run: MonitorRun }>(`/api/price-monitor/projects/${id}/run-now`, {});
export const fetchMonitorRun = (id: string) => api.get<{ run: MonitorRun }>(`/api/price-monitor/runs/${id}`);
export const fetchMonitorMailPreview = (id: string) =>
  api.get<{ subject: string; html: string; recipients: string[] }>(`/api/price-monitor/runs/${id}/mail-preview`);

export async function downloadMonitorRun(id: string): Promise<void> {
  const token = getToken();
  const res = await fetch(`/api/price-monitor/runs/${id}/export.xlsx`, { headers: token ? { Authorization: 'Bearer ' + token } : {} });
  if (res.status === 401) clearAuth();
  if (!res.ok) {
    let msg = '다운로드 실패 (HTTP ' + res.status + ')';
    try { const d = await res.json(); if (typeof d?.error === 'string') msg = d.error; } catch { /* 본문 없음 */ }
    throw new ApiError(msg, res.status);
  }
  const blob = await res.blob();
  const m = (res.headers.get('Content-Disposition') || '').match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  try { a.href = url; a.download = m ? decodeURIComponent(m[1]) : `monitoring_${id.slice(0, 8)}.xlsx`; document.body.appendChild(a); a.click(); }
  finally { a.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000); }
}

export const COUNTRY_KO: Record<string, string> = { JP: '일본', IT: '이탈리아', FR: '프랑스', CH: '스위스', UK: '영국', DE: '독일', CA: '캐나다', US: '미국' };
export const KIND_KO: Record<MonitorKind, string> = {
  first: '첫 확인', same: '변동 없음', price: '가격 변동', basis: '기준·출처 변경', packs: '포장 구성 변경',
  now_priced: '새로 확인', lost: '미확인 전환', unpriced: '가격 미확인',
};
export const VERDICT_KO: Record<string, string> = {
  not_marketed: '미허가·미출시 확인', not_listed: '가격·급여 목록 미등재', no_public_price: '공개 가격 없음(판매 확인)',
  access_blocked: '접속 차단 — 검증 불가', unresolved: '판단 불가',
};
export const RUN_STATUS_KO: Record<MonitorRunStatus, string> = {
  scheduled: '예약', collecting: '수집 중', ready: '발송 대기', mailing: '발송 중', sent: '발송 완료', failed: '확인 필요', cancelled: '취소',
};
