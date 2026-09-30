import { api } from './client';

// GET /api/admin/daily-mailing/kanban 계약 — agents/daily_mailing/storage.py:load_admin_kanban()
// 헤르메스 run 번들 스키마 (docs/daily_mailing/KANBAN_MIGRATION_SPEC_20260706.md)
// 기사별 승인 없음(article_approval_required=false), 읽기 전용 운영 상태 board.
// 주의: 필드는 null/부재 가능 — 소비 측은 방어적으로 처리한다.

export interface ReviewerFinding {
  reviewer?: string | null;
  label?: string | null;
  decision?: string | null; // pass | warn | fix ...
  rationale?: string | null;
  required_fix?: string | null;
}

export interface KanbanArticle {
  article_id: string;
  run_id: string;
  title: string;
  publisher_url: string | null;
  naver_url: string | null;
  official_url?: string | null;
  source_name: string | null;
  source_tier: string | null;
  source_status: string | null;
  priority: string | null; // High | Medium | Watch
  ma_relevance: number | null;
  review_status: string | null; // needs_review | ready_for_writer | excluded
  tracking_lane?: string | null; // daily_monitoring | keytruda_source_verification | policy_pricing_tracker
  selected_for_draft: number | boolean;
  score: number;
  published_at: string | null;
  keyword?: string | null;
  verification_caveat: string | null;
  verification_method?: string | null;
  next_action?: string | null;
  expires_at?: string;
  created_at?: string;
  quality_flags?: string[] | null;
  matched_keywords?: string[] | null;
  tracker_tags?: string[] | null;
  persona_ids?: string[] | null;
  // 배열 또는 단일 객체로 도착 가능 — normalizeReviewerFindings() 로 정규화
  reviewer_findings?: ReviewerFinding[] | ReviewerFinding | null;
  generated_at: string | null;
  html_path: string | null;
}

export interface KanbanLane {
  name: string;
  items: KanbanArticle[];
}

export interface QualityReport {
  status?: string | null;
  sendable?: boolean | null;
  live_send_allowed?: boolean | null;
  total_articles?: number | null;
  top_signal_count?: number | null;
  watchlist_count?: number | null;
  min_total_articles?: number | null;
  min_top_signals?: number | null;
  blocking_reasons?: string[] | null;
  warnings?: string[] | null;
}

export interface RunCounts {
  discovered?: number | null;
  recent?: number | null;
  selected?: number | null;
  needs_review?: number | null;
  ready_for_writer?: number | null;
}

// 브리프 항목의 영향 방향 — agents/daily_mailing/brief.py:BriefItem.impact
// threat|opportunity|neutral 은 시사점이 렌더될 때만, 시사점이 없으면 "none" 또는 "" (구 run 은 키 자체가 없음)
export type DraftImpact = 'threat' | 'opportunity' | 'neutral' | 'none' | '';

// 같은 사건을 보도한 다른 매체 (클러스터링으로 흡수된 기사) — BriefItem.also_reported_by
export interface AlsoReportedBy {
  source_name?: string | null;
  url?: string | null;
}

export interface DraftItem {
  title?: string | null;
  description?: string | null;
  publisher_url?: string | null;
  evidence_quotes?: string[] | null;
  monitoring_point?: string | null;
  work_note?: string | null;
  // 2026-09 엔진 브리프(BriefItem.to_dict) 추가 필드 — 구 run 의 draft_items 에는 없다
  impact?: DraftImpact | string | null;
  affected_asset?: string | null;
  also_reported_by?: AlsoReportedBy[] | null;
}

export interface KanbanRun {
  run_id: string;
  generated_at: string;
  window_label: string | null;
  lookback_hours: number;
  keywords_json: string;
  media_json: string;
  subscription_id: number | null;
  owner_email: string | null;
  recipients_json: string;
  delivery_status: string;
  approval_status: string;
  gmail_draft_id: string | null;
  gmail_message_id: string | null;
  sent_at: string | null;
  discovered_count: number;
  recent_count: number;
  selected_count: number;
  status: string;
  markdown_path: string | null;
  html_path: string | null;
  json_path: string | null;
  review_board_path: string | null;
  expires_at: string;
  created_at: string;
  quality_report?: QualityReport | null;
  counts?: RunCounts | null;
  draft_items?: DraftItem[] | null;
  dashboard_scope?: Record<string, unknown> | null;
  run_date?: string | null;
  is_test?: number | boolean | null;
  email_subject?: string | null;
  has_email_html?: boolean;
  send_attempts?: number | null;
  delivery_error?: string | null;
  approved_by?: string | null;
  approved_at?: string | null;
}

export interface OperatingPolicy {
  board_purpose?: string | null;
  article_approval_required?: boolean | null;
  live_send_allowed?: boolean | null;
  reviewer_roles_are_advisory?: boolean | null;
  personas_are_audience_targeting_metadata?: boolean | null;
  sendable_requires?: string[] | null;
}

export interface Persona {
  persona_id: string;
  label?: string | null;
  description?: string | null;
  default_keywords?: string[] | null;
  priority_terms?: string[] | null;
  watch_terms?: string[] | null;
  content_requirements?: string[] | null;
}

export interface ReviewerRole {
  role_id: string;
  label?: string | null;
  description?: string | null;
  required_checks?: string[] | null;
}

export interface DailyMailingKanban {
  status: string;
  retention_days: number;
  article_approval_required: boolean;
  operating_policy?: OperatingPolicy | null;
  personas?: Persona[] | null;
  reviewer_roles?: ReviewerRole[] | null;
  lanes: KanbanLane[];
  runs: KanbanRun[];
}

export async function fetchDailyMailingKanban(): Promise<DailyMailingKanban> {
  return api.get<DailyMailingKanban>('/api/admin/daily-mailing/kanban');
}

// runs 테이블의 *_json 컬럼은 raw JSON 문자열 그대로 온다 — 파싱 실패 시 빈 배열로 폴백
export function parseJsonArray(raw: string | null | undefined): string[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch {
    return [];
  }
}

// null/undefined/단일값/배열 → 문자열 배열로 정규화
export function asStringArray(value: unknown): string[] {
  if (value == null) return [];
  if (Array.isArray(value)) return value.filter(v => v != null).map(String);
  if (typeof value === 'string') return value ? [value] : [];
  return [];
}

// reviewer_findings 는 배열 또는 단일 객체로 도착할 수 있다 — 항상 배열로 정규화
export function normalizeReviewerFindings(
  raw: ReviewerFinding[] | ReviewerFinding | null | undefined,
): ReviewerFinding[] {
  if (raw == null) return [];
  if (Array.isArray(raw)) return raw.filter((f): f is ReviewerFinding => f != null && typeof f === 'object');
  if (typeof raw === 'object') return [raw];
  return [];
}

// impact 원값 → 렌더 키. threat|opportunity|neutral 만 배지를 그린다 ("none"/""/부재/이상값은 null → 배지 없음)
export function normalizeImpact(value: unknown): 'threat' | 'opportunity' | 'neutral' | null {
  if (typeof value !== 'string') return null;
  const v = value.trim().toLowerCase();
  return v === 'threat' || v === 'opportunity' || v === 'neutral' ? v : null;
}

// http(s) URL 만 링크로 허용 (메일 렌더 brief.py:_safe_link 와 동일 정책) — 그 외는 null
export function safeHttpUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const v = value.trim();
  if (!v) return null;
  try {
    const u = new URL(v);
    return u.protocol === 'http:' || u.protocol === 'https:' ? v : null;
  } catch {
    return null;
  }
}

export const ALSO_REPORTED_MAX_NAMES = 3;

export interface AlsoReportedEntry {
  name: string;
  href: string | null;
}

// '외 N개 매체: 이름 ≤3 (외 M곳)' 계산 — 메일의 brief.py:_also_reported_parts 와 동일 규칙.
// 이름은 source_name, 없으면 URL 의 host (긴 링크 목록 대신 매체명만). 둘 다 없으면 건너뛴다.
export function alsoReportedParts(
  raw: AlsoReportedBy[] | null | undefined,
): { total: number; shown: AlsoReportedEntry[]; rest: number } {
  if (!Array.isArray(raw)) return { total: 0, shown: [], rest: 0 };
  const entries: AlsoReportedEntry[] = [];
  for (const e of raw) {
    if (e == null || typeof e !== 'object') continue;
    const href = safeHttpUrl(e.url);
    let name = typeof e.source_name === 'string' ? e.source_name.trim() : '';
    if (!name && href) {
      try { name = new URL(href).hostname; } catch { name = ''; }
    }
    if (!name) continue;
    entries.push({ name, href });
  }
  const shown = entries.slice(0, ALSO_REPORTED_MAX_NAMES);
  return { total: entries.length, shown, rest: entries.length - shown.length };
}

export interface RunActionResult {
  ok: boolean;
  run: {
    run_id: string;
    delivery_status: string;
    approval_status: string;
    sent_at: string | null;
    delivery_error: string | null;
  };
}

export async function approveDailyMailingRun(runId: string): Promise<RunActionResult> {
  return api.post<RunActionResult>(`/api/admin/daily-mailing/runs/${encodeURIComponent(runId)}/approve`, {});
}

export async function rejectDailyMailingRun(runId: string): Promise<RunActionResult> {
  return api.post<RunActionResult>(`/api/admin/daily-mailing/runs/${encodeURIComponent(runId)}/reject`, {});
}

export interface RunPreview {
  subject: string;
  html: string;
  delivery_status: string | null;
}

export async function fetchDailyMailingRunPreview(runId: string): Promise<RunPreview> {
  return api.get<RunPreview>(`/api/admin/daily-mailing/runs/${encodeURIComponent(runId)}/preview`);
}

export interface HealthProblem {
  subscription_id: number | null;
  name: string;
  problem: string;
  detail?: string | null;
}

export interface DailyMailingHealth {
  run_date: string;
  checked_at: string;
  ok: boolean;
  problems: HealthProblem[];
  gmail_ready: boolean;
}

export async function fetchDailyMailingHealth(): Promise<DailyMailingHealth> {
  return api.get<DailyMailingHealth>('/api/admin/daily-mailing/health');
}
