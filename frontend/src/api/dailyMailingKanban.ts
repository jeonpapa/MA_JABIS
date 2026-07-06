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

export interface DraftItem {
  title?: string | null;
  description?: string | null;
  publisher_url?: string | null;
  evidence_quotes?: string[] | null;
  monitoring_point?: string | null;
  work_note?: string | null;
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
