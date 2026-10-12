import { api, ApiError, clearAuth, getToken } from './client';

// ── Stakeholder Signals (정책 발언 인텔리전스) API 모듈 ──────────────────────
// 계약: docs/superpowers/plans/2026-10-12-stakeholder-verbatim-migration.md §Task 6 "API 계약".
// 행 모양은 agents/stakeholder/store.py (list_events / list_statements / get_statement / list_quotes)
// 반환 dict 를 따른다. 방어적 타이핑(serviceRequests.ts 선례): 대부분 optional/nullable.
//
// 증거 하이진(agents/rules/stakeholder_verbatim_rules.md §4·§5):
//   - 사용자 화면은 locator 창 발췌(≤600자)만 본다 — 전문(`/text`)·원본(`/raw`) admin 라우트는 이 모듈이 호출하지 않는다.
//   - 인물 사진은 인증 라우트 `/api/stakeholder/people/<id>/photo` 만 쓴다. 언론 CDN 링크는 렌더링 금지.

export type SVGradeClass = 'a' | 'b' | 'c' | 'role';
export type SVIntegrityStatus = 'verified' | 'mismatch' | 'missing' | 'no_local' | 'unchecked';
export type SVLocatorMatch = 'range' | 'file' | 'none' | 'unchecked';
export type SVTopicSlug = 'price_reform' | 'access' | 'rwe' | 'finance' | 'negotiation';
export type SVRunKind = 'monitoring' | 'audit';

/** 사용자에게 보이는 원문 발췌 상한 (서버 계약과 동일 — 클라이언트에서도 강제). */
export const EXCERPT_MAX_CHARS = 600;

// ── 공용 라벨 ────────────────────────────────────────────────────────────────

/** 주제 slug → 탭 라벨. 정본 MD 제목(`# N. 주제 ①…`)과 같은 축. 서버가 overview.topics 를 주면 그 제목이 우선. */
export const TOPIC_ORDER: SVTopicSlug[] = ['price_reform', 'access', 'rwe', 'finance', 'negotiation'];
export const TOPIC_LABELS: Record<string, string> = {
  price_reform: '약가제도 개편·재평가',
  access: '신약 접근성·신속등재',
  rwe: 'RWE·성과기반평가',
  finance: '건보 재정·지출관리',
  negotiation: '약가협상·공급·사후관리',
};

/** 증거등급 어휘 7종 (sv_meta evidence_grade_vocabulary). */
export const GRADE_LABELS: Record<string, string> = {
  A: '공식 원문',
  'B-quote': '기관 자료 직접인용',
  'B-summary': '기관 자료 요약',
  'C-quote': '언론 직접인용',
  'C-summary': '언론 요약',
  'Role-only': '직책만 확인',
  'Candidate-only': '후보(미확인)',
};

export const GRADE_CLASS_LABELS: Record<SVGradeClass, string> = {
  a: 'A 공식 원문',
  b: 'B 기관 자료·인용',
  c: 'C 언론 인용·요약',
  role: 'Role-only 직책 확인',
};

export const ORG_FILTERS: { key: string; label: string }[] = [
  // `org` 쿼리 값 — 서버 ORG_PATTERNS 가 speaker_role_at_event / person.organization 과 매칭
  { key: '보건복지부', label: '보건복지부' },
  { key: '심평원', label: '건강보험심사평가원' },
  { key: '공단', label: '국민건강보험공단' },
];

export const SOURCE_TYPE_LABELS: Record<string, string> = {
  official_transcript_hwp: '공식 회의록',
  official_press_release: '공식 보도자료',
  official_video: '공식 영상',
  official_department_duties: '공식 업무분장',
  official_event_metadata: '공식 행사정보',
  professional_media: '전문언론',
  professional_media_direct_quotes: '전문언론 직접인용',
  professional_media_event_summary: '전문언론 행사요약',
  professional_media_period_outside: '전문언론(조사기간 외)',
  user_provided_image: '사용자 제공 이미지',
};

// ── 타입 ────────────────────────────────────────────────────────────────────

export interface SVPersonRef {
  person_id: string;
  name?: string | null;
  /** 내부(admin 업로드) 사진 존재 여부. true 일 때만 personPhotoUrl() 을 인증 fetch. */
  photo?: boolean | null;
}

export interface SVOverview {
  scope?: { start?: string | null; end?: string | null } | null;
  kpi?: {
    events?: number;
    statements?: number;
    quotes?: number;
    speakers_observed?: number;
    people_active?: number;
    people_priority?: number;
    people_candidates?: number;
    pending_proposals?: number;
  } | null;
  latest_monitoring_run?: {
    run_id?: string;
    run_date?: string | null;
    run_status?: string | null;
    saturation_status?: string | null;
    active_people?: number | null;
    approved_domains?: number | null;
    new_quotes?: number | null;
  } | null;
  latest_audit_run?: {
    audit_run_id?: string;
    run_date?: string | null;
    run_status?: string | null;
    claim?: string | null;
    why_this_matters?: string | null;
    proposals_created?: number | null;
  } | null;
  recent_events?: {
    event_id: string;
    event_date?: string | null;
    title?: string | null;
    topics?: string[] | null;
    speakers?: string[] | null;
  }[] | null;
  integrity?: {
    sources_verified?: number;
    sources_total?: number;
    mismatch?: number;
    missing?: number;
    last_sync_at?: string | null;
  } | null;
  copyright_notice?: string | null;
  /** 선택 확장: 정본 MD 주제 제목. 없으면 TOPIC_LABELS. */
  topics?: { slug: string; title?: string | null }[] | null;
}

export interface SVEvent {
  event_id: string;
  event_date?: string | null;
  title?: string | null;
  context?: string | null;
  publication_dates?: string[] | null;
  topics?: string[] | null;
  speakers?: string[] | null;
  source_ids?: string[] | null;
  grades?: string[] | null;
  statements_count?: number | null;
  headline?: {
    speaker_name?: string | null;
    person?: SVPersonRef | null;
    quote_text?: string | null;
    evidence_grade?: string | null;
  } | null;
}

/** 표 행의 대표 인용. 계약 해석: 객체 `{quote_id, quote_text, locator_match}` (문자열이 와도 수용). */
export interface SVHeadlineQuote {
  quote_id?: string | null;
  quote_text: string;
  locator_match?: SVLocatorMatch | string | null;
}

export interface SVStatementSource {
  source_id: string;
  publisher_title?: string | null;
  url?: string | null;
  publication_date?: string | null;
  integrity_status?: SVIntegrityStatus | string | null;
}

export interface SVStatementRow {
  statement_id: string;
  event?: { event_id: string; event_date?: string | null; title?: string | null } | null;
  topic?: string | null;
  speaker_name?: string | null;
  speaker_role_at_event?: string | null;
  person?: SVPersonRef | null;
  evidence_grade?: string | null;
  grade_class?: SVGradeClass | string | null;
  headline_quote?: SVHeadlineQuote | string | null;
  summary_text?: string | null;
  quotes_count?: number | null;
  source?: SVStatementSource | null;
}

export interface SVQuote {
  quote_id: string;
  /** 원문 그대로 (곡괄호·생략표시 포함). 화면은 가공 없이 표시한다. */
  quote_text: string;
  segments?: string[] | null;
  locator?: string | null;
  locator_match?: SVLocatorMatch | string | null;
  topics?: string[] | null;
  /** store sv_quote 컬럼 — 인용별 출처 링크·등급. 없으면 발언 블록의 provenance/등급으로 폴백. */
  source_id?: string | null;
  evidence_grade?: string | null;
}

export interface SVProvenance {
  source_id: string;
  title?: string | null;
  url?: string | null;
  source_type?: string | null;
  evidence_grade?: string | null;
  publication_date?: string | null;
  sha256?: string | null;
  text_sha256?: string | null;
  integrity_status?: SVIntegrityStatus | string | null;
  last_live_check?: { checked_at?: string | null; http_status?: number | null; content_type?: string | null } | null;
}

export interface SVExcerpt {
  source_id?: string | null;
  locator?: string | null;
  text: string;
  start?: number | null;
  end?: number | null;
  chars?: number | null;
}

export interface SVStatementDetail {
  statement_id: string;
  event_id?: string | null;
  event_date?: string | null;
  event_title?: string | null;
  event?: { event_id: string; event_date?: string | null; title?: string | null } | null;
  topic?: string | null;
  speaker_name?: string | null;
  speaker_role_at_event?: string | null;
  person_id?: string | null;
  person?: SVPersonRef | null;
  multi_speaker?: boolean | null;
  summary_text?: string | null;
  analysis_text?: string | null;
  limitation_text?: string | null;
  policy_status?: string | null;
  evidence_grade?: string | null;
  grade_class?: SVGradeClass | string | null;
  locators?: { path: string; start?: number | null; end?: number | null }[] | null;
  source_ids?: string[] | null;
  source_url?: string | null;
  role_check_url?: string | null;
  publication_date?: string | null;
  headline_quote_id?: string | null;
  quotes?: SVQuote[] | null;
  provenance?: SVProvenance[] | null;
  excerpt?: SVExcerpt | null;
}

export interface SVRoleHistory {
  id?: number;
  organization?: string | null;
  department?: string | null;
  role?: string | null;
  effective_from?: string | null;
  effective_to?: string | null;
  status?: string | null;
  proposal_id?: string | null;
  approved_at?: string | null;
  applied_at?: string | null;
}

export interface SVPerson {
  person_id: string;
  name: string;
  organization?: string | null;
  organization_code?: string | null;
  department?: string | null;
  role?: string | null;
  status?: 'active' | 'paused' | string | null;
  verification_status?: 'verified' | 'candidate' | string | null;
  effective_from?: string | null;
  aliases?: string[] | null;
  topic_tags?: string[] | null;
  has_photo?: boolean | null;
  statements_count?: number | null;
  importance_score?: number | null;
  importance?: Record<string, unknown> | null;
  discovered_from_proposal?: string | null;
  origin?: string | null;
  updated_by?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SVPersonDetail extends SVPerson {
  role_history?: SVRoleHistory[] | null;
  recent_statements?: SVStatementRow[] | null;
  /** admin 응답에만 있는 소스 메타(언론 CDN 링크). 절대 <img> 로 렌더링하지 않는다 — 텍스트만. */
  photo_url?: string | null;
}

export interface SVPeopleList {
  active_effective_count?: number;
  updated_at?: string | null;
  items: SVPerson[];
}

export interface SVSource {
  source_id: string;
  title?: string | null;
  url?: string | null;
  source_type?: string | null;
  evidence_grade?: string | null;
  access_status?: string | null;
  sha256?: string | null;
  text_sha256?: string | null;
  bytes?: number | null;
  event_date?: string | null;
  publication_date?: string | null;
  locator?: string | null;
  limitation?: string | null;
  integrity_status?: SVIntegrityStatus | string | null;
  integrity_checked_at?: string | null;
  last_live_check?: SVProvenance['last_live_check'];
  /** admin 응답에만 존재 */
  local_path?: string | null;
  text_path?: string | null;
}

export interface SVRun {
  run_id?: string;
  audit_run_id?: string;
  run_date?: string | null;
  executed_at?: string | null;
  completed_at?: string | null;
  run_status?: string | null;
  saturation_status?: string | null;
  active_people?: number | null;
  approved_domains?: number | null;
  new_quotes?: number | null;
  claim?: string | null;
  why_this_matters?: string | null;
  proposals_created?: number | null;
  imported_at?: string | null;
}

export interface SVRunDetail extends SVRun {
  payload?: Record<string, unknown> | null;
  md?: string | null;
}

export interface SVProposalEvent {
  id?: number;
  proposal_id?: string;
  actor?: string | null;
  event?: string | null;
  detail?: string | null;
  created_at?: string | null;
}

export interface SVProposalEvidence {
  url?: string | null;
  publisher?: string | null;
  /** 증거수준 E0~E4 (소스 AuditEvidence.evidence_level). `level` 은 구 표기 폴백. */
  evidence_level?: string | null;
  level?: string | null;
  source_class?: string | null;
  quoted_text?: string | null;
  locator?: string | null;
  publication_date?: string | null;
  effective_date?: string | null;
  retrieved_at?: string | null;
  independence_group?: string | null;
  [k: string]: unknown;
}

/** 충돌 index 별 확인 상태 (T2b approval.conflicts_status). stale = 확인 뒤 충돌 문구가 바뀌어 확인이 무효. */
export interface SVConflictStatus {
  index: number;
  text: string;
  acknowledged: boolean;
  stale?: boolean;
  by?: string | null;
  at?: string | null;
  reason?: string | null;
}

/** 확인 기록 행 (GET …/conflict-acks). stale 이 된 이전 확인의 당시 문구·사유를 보여줄 때 쓴다. */
export interface SVConflictAck {
  proposal_id?: string;
  conflict_index: number;
  conflict_text?: string | null;
  reason?: string | null;
  acknowledged_by?: string | null;
  acknowledged_at?: string | null;
}

export interface SVProposal {
  proposal_id: string;
  proposal_type: 'source_expansion' | 'role_change' | 'new_person' | string;
  dedupe_key?: string;
  priority?: 'urgent' | 'high' | 'normal' | 'low' | string;
  summary?: string;
  rationale?: string;
  proposed_change?: Record<string, unknown> | null;
  evidence?: SVProposalEvidence[] | null;
  conflicts?: unknown[] | null;
  /** 서버(T2b) 부착 — 충돌마다 확인 상태. 없으면 클라이언트는 모든 충돌을 미확인으로 본다(보수적). */
  conflicts_status?: SVConflictStatus[] | null;
  /** 미확인(또는 stale) 충돌 index. 비어 있어야 승인 가능. */
  conflicts_unacknowledged?: number[] | null;
  audit_run_id?: string | null;
  created_by?: string | null;
  status?: string;
  effective_from?: string | null;
  reviewer?: string | null;
  reviewed_at?: string | null;
  decision_reason?: string | null;
  applied_at?: string | null;
  origin?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  events?: SVProposalEvent[] | null;
}

export interface SVProposalList {
  pending_count?: number;
  updated_at?: string | null;
  proposals: SVProposal[];
}

export interface SVSourceDomain {
  source_id: string;
  name?: string;
  domain?: string;
  source_group?: string;
  status?: string;
  effective_from?: string | null;
  approved_from_proposal?: string | null;
  evidence?: unknown;
  origin?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SVPersonInput {
  name?: string;
  organization?: string;
  department?: string;
  role?: string;
  status?: 'active' | 'paused';
  aliases?: string[];
  topic_tags?: string[];
  organization_code?: string;
}

// ── 순수 헬퍼 ────────────────────────────────────────────────────────────────

function qs(params: Record<string, string | number | null | undefined>): string {
  const sp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && String(v) !== '') sp.set(k, String(v));
  });
  const s = sp.toString();
  return s ? `?${s}` : '';
}

/** 증거등급 → 배지 클래스. 서버 grade_class 우선, 없으면 첫 토큰으로(`C-quote/C-summary` → c).
 *  서버 grade_class_of_token 과 같은 규칙: 토큰이 정확히 A/B/C 이거나 `A-`/`B-`/`C-` 로 시작해야 그 군이다 —
 *  `Candidate-only` 는 C 로 시작하지만 C 군이 아니라 role. */
export function gradeClassOf(grade?: string | null, serverClass?: string | null): SVGradeClass {
  const sc = (serverClass || '').toLowerCase();
  if (sc === 'a' || sc === 'b' || sc === 'c' || sc === 'role') return sc;
  const tok = (grade || '').split(/[/+]/)[0]?.trim() ?? '';
  for (const k of ['A', 'B', 'C'] as const) {
    if (tok === k || tok.startsWith(`${k}-`)) return k.toLowerCase() as SVGradeClass;
  }
  return 'role';
}

/** 증거등급 사람이 읽는 라벨. 복합(`C-quote/C-summary`)은 토큰별로 이어 붙인다. */
export function gradeLabel(grade?: string | null): string {
  if (!grade) return '등급 미표기';
  return grade
    .split(/[/+]/)
    .map(t => t.trim())
    .filter(Boolean)
    .map(t => GRADE_LABELS[t] ?? t)
    .join(' / ');
}

/** 주제 탭 목록 + 라벨 맵. overview.topics(정본 MD 제목)가 있으면 우선, 없으면 TOPIC_LABELS. */
export function topicsFrom(overview?: SVOverview | null): {
  list: { slug: string; label: string }[];
  labels: Record<string, string>;
} {
  const labels: Record<string, string> = { ...TOPIC_LABELS };
  (overview?.topics ?? []).forEach(t => {
    if (t?.slug && t.title) labels[t.slug] = t.title;
  });
  const extra = (overview?.topics ?? []).map(t => t.slug).filter(s => s && !TOPIC_ORDER.includes(s as SVTopicSlug));
  const list = [...TOPIC_ORDER, ...extra].map(slug => ({ slug, label: labels[slug] ?? slug }));
  return { list, labels };
}

export function headlineQuoteOf(row: Pick<SVStatementRow, 'headline_quote'>): SVHeadlineQuote | null {
  const h = row.headline_quote;
  if (!h) return null;
  if (typeof h === 'string') return h.trim() ? { quote_text: h } : null;
  return h.quote_text ? h : null;
}

/** 외부 링크는 http(s) 만 href 로 쓴다. */
export function safeHttpUrl(url?: string | null): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return u.protocol === 'http:' || u.protocol === 'https:' ? u.toString() : null;
  } catch {
    return null;
  }
}

export function sourceTypeLabel(t?: string | null): string {
  if (!t) return '—';
  return SOURCE_TYPE_LABELS[t] ?? t;
}

/** 사용자 화면 발췌 상한 강제 — 서버가 실수로 더 보내도 600자를 넘겨 그리지 않는다. */
export function clampExcerpt(text?: string | null): { text: string; clamped: boolean } {
  const t = text ?? '';
  if (t.length <= EXCERPT_MAX_CHARS) return { text: t, clamped: false };
  return { text: t.slice(0, EXCERPT_MAX_CHARS), clamped: true };
}

// ── 인증 바이너리 (사진·관계도) ──────────────────────────────────────────────

async function fetchAuthBlob(path: string): Promise<Blob | null> {
  const token = getToken();
  const res = await fetch(path, { headers: token ? { Authorization: `Bearer ${token}` } : undefined });
  if (res.status === 404) return null;
  if (!res.ok) {
    if (res.status === 401) clearAuth();
    throw new ApiError(`HTTP ${res.status}`, res.status);
  }
  return res.blob();
}

// ── user 읽기 ───────────────────────────────────────────────────────────────

export function fetchOverview(): Promise<SVOverview> {
  return api.get<SVOverview>('/api/stakeholder/overview');
}

export interface SVEventParams {
  topic?: string;
  org?: string;
  grade?: string;
  from?: string;
  to?: string;
}

export async function fetchEvents(params: SVEventParams = {}): Promise<SVEvent[]> {
  const res = await api.get<{ items?: SVEvent[] }>(`/api/stakeholder/events${qs({ ...params })}`);
  return res?.items ?? [];
}

export interface SVStatementParams {
  topic?: string;
  org?: string;
  grade?: string;
  person_id?: string;
  q?: string;
  limit?: number;
}

export async function fetchStatements(params: SVStatementParams = {}): Promise<SVStatementRow[]> {
  const res = await api.get<{ items?: SVStatementRow[] }>(`/api/stakeholder/statements${qs({ ...params })}`);
  return res?.items ?? [];
}

export function fetchStatement(id: string): Promise<SVStatementDetail> {
  return api.get<SVStatementDetail>(`/api/stakeholder/statements/${encodeURIComponent(id)}`);
}

export async function fetchPeople(params: { status?: string; q?: string } = {}): Promise<SVPeopleList> {
  const res = await api.get<SVPeopleList>(`/api/stakeholder/people${qs(params)}`);
  return { ...res, items: res?.items ?? [] };
}

export function fetchPerson(id: string): Promise<SVPersonDetail> {
  return api.get<SVPersonDetail>(`/api/stakeholder/people/${encodeURIComponent(id)}`);
}

/** 인증 사진 라우트 경로. <img src> 로 직접 쓰지 말 것(Authorization 필요) — fetchPersonPhotoObjectUrl 사용. */
export function personPhotoUrl(id: string): string {
  return `/api/stakeholder/people/${encodeURIComponent(id)}/photo`;
}

const photoCache = new Map<string, Promise<string | null>>();

/** 내부 사진(webp) → objectURL. 404/오류는 null(이니셜 폴백). 세션 동안 인물당 1회만 요청. */
export function fetchPersonPhotoObjectUrl(id: string): Promise<string | null> {
  let p = photoCache.get(id);
  if (!p) {
    p = fetchAuthBlob(personPhotoUrl(id))
      .then(b => (b ? URL.createObjectURL(b) : null))
      .catch(() => null);
    photoCache.set(id, p);
  }
  return p;
}

export async function fetchSources(): Promise<SVSource[]> {
  const res = await api.get<{ items?: SVSource[] } | SVSource[]>('/api/stakeholder/sources');
  if (Array.isArray(res)) return res;
  return res?.items ?? [];
}

/** locator 창 발췌(≤600자). locator 는 `sources/<file>.txt:a-b` 형식 그대로. */
export function fetchExcerpt(sourceId: string, locator: string): Promise<SVExcerpt> {
  return api.get<SVExcerpt>(
    `/api/stakeholder/sources/${encodeURIComponent(sourceId)}/excerpt${qs({ locator })}`,
  );
}

export function relationshipMapUrl(): string {
  return '/api/stakeholder/artifacts/relationship-map.svg';
}

/** 관계도 SVG → objectURL (없으면 null). 호출자가 URL.revokeObjectURL 로 정리. */
export async function fetchRelationshipMapObjectUrl(): Promise<string | null> {
  const b = await fetchAuthBlob(relationshipMapUrl());
  if (!b) return null;
  // <img> 로만 표시 — SVG 내 스크립트는 이미지 컨텍스트에서 실행되지 않는다
  const svg = b.type.includes('svg') ? b : new Blob([b], { type: 'image/svg+xml' });
  return URL.createObjectURL(svg);
}

export async function fetchRuns(kind: SVRunKind, limit = 10): Promise<SVRun[]> {
  const res = await api.get<{ items?: SVRun[] } | SVRun[]>(`/api/stakeholder/runs${qs({ kind, limit })}`);
  if (Array.isArray(res)) return res;
  return res?.items ?? [];
}

export function fetchRun(kind: SVRunKind, runId: string): Promise<SVRunDetail> {
  return api.get<SVRunDetail>(`/api/stakeholder/runs/${kind}/${encodeURIComponent(runId)}`);
}

// ── admin (T7b 화면용) ──────────────────────────────────────────────────────
//
// 오류 형식: admin 라우트는 `{code, message, details}`(T2b ApprovalError) 또는 기존 관례 `{error, code}` 를 돌려준다.
// client.ts 의 ApiError 는 `error` 만 읽고 details 를 버리므로, admin 호출은 svRequest 로 감싸
// 한국어 `message`(없으면 `error`)·`code`·`details` 를 그대로 보존한다(화면이 서버 문구를 그대로 보여준다).

/** 승인·반려·충돌 확인 사유 길이 (approval.REASON_MIN / REASON_MAX — 서버와 동일). */
export const REASON_MIN = 2;
export const REASON_MAX = 1000;

/** 사진 업로드 허용 형식·상한 (서버 photos.normalize 가 WEBP 640×640 으로 재인코딩; 415/413 은 서버 문구 그대로). */
export const PHOTO_ACCEPT = ['image/jpeg', 'image/png', 'image/webp'] as const;
export const PHOTO_MAX_BYTES = 5 * 1024 * 1024;

/** 열린(결정 대기) 제안 상태 — 사이드바 배지·"검토 필요" 칩의 정의. */
export const OPEN_PROPOSAL_STATUSES = ['needs_review', 'needs_evidence'] as const;

export const PROPOSAL_TYPE_LABELS: Record<string, string> = {
  source_expansion: '매체 확장',
  role_change: '현재 직책 변경',
  new_person: '신규 인물',
};

export const PROPOSAL_STATUS_LABELS: Record<string, string> = {
  needs_review: '검토 필요',
  needs_evidence: '근거 보완',
  approved: '승인',
  scheduled: '적용 예정',
  applied: '적용 완료',
  application_failed: '적용 실패',
  rejected: '반려',
  superseded: '대체됨',
};

export const PROPOSAL_EVENT_LABELS: Record<string, string> = {
  created: '생성',
  conflict_acknowledged: '충돌 확인',
  approved: '승인',
  rejected: '반려',
  applied: '반영',
  application_failed: '반영 실패',
};

export const SOURCE_GROUP_LABELS: Record<string, string> = {
  official: '공식',
  top_tier_general: '종합 일간지',
  healthcare_trade: '보건의료 전문지',
  discovered_additional: '추가 발견',
};

/** 사이드바 배지 갱신 신호 — 감사 페이지가 결정 직후 발행, Sidebar 가 구독(60초 폴링을 기다리지 않게). */
export const SV_PROPOSALS_CHANGED_EVENT = 'sv-proposals-changed';

export function notifyProposalsChanged(): void {
  try {
    window.dispatchEvent(new Event(SV_PROPOSALS_CHANGED_EVENT));
  } catch {
    /* 비브라우저 환경 — 무시 */
  }
}

export class SVApiError extends ApiError {
  details: Record<string, unknown> | null;
  constructor(message: string, status: number, code?: string, details?: Record<string, unknown> | null) {
    super(message, status, code);
    this.name = 'SVApiError';
    this.details = details ?? null;
  }
}

function toSvError(status: number, data: unknown): SVApiError {
  const d = (data && typeof data === 'object' ? data : {}) as {
    message?: unknown;
    error?: unknown;
    code?: unknown;
    details?: unknown;
  };
  const pick = (v: unknown) => (typeof v === 'string' && v.trim() ? v.trim() : '');
  const message =
    pick(d.message) ||
    pick(d.error) ||
    (typeof data === 'string' && data.trim() && data.length < 400 ? data.trim() : '') ||
    `요청을 처리하지 못했습니다 (HTTP ${status})`;
  const code = pick(d.code) || undefined;
  const details = d.details && typeof d.details === 'object' ? (d.details as Record<string, unknown>) : null;
  return new SVApiError(message, status, code, details);
}

async function svRequest<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload: FormData | string | undefined;
  if (body instanceof FormData) {
    payload = body; // multipart 경계는 브라우저가 Content-Type 에 넣는다
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  const res = await fetch(path, { method, headers, body: payload });
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    if (res.status === 401) clearAuth();
    throw toSvError(res.status, data);
  }
  return data as T;
}

/** 화면 표시용 오류 문구 — 서버 한국어 message 우선. */
export function svErrorMessage(e: unknown, fallback = '요청을 처리하지 못했습니다.'): string {
  if (e instanceof Error && e.message) return e.message;
  return fallback;
}

/** CONFLICTS_UNACKNOWLEDGED 422 의 details.unacknowledged → index 목록 (그 외 오류는 []). */
export function unacknowledgedFromError(e: unknown): number[] {
  if (!(e instanceof SVApiError) || e.code !== 'CONFLICTS_UNACKNOWLEDGED') return [];
  const raw = e.details?.unacknowledged;
  return Array.isArray(raw) ? raw.filter((n): n is number => typeof n === 'number' && Number.isInteger(n)) : [];
}

/** 제안의 충돌 상태 — 서버 conflicts_status 우선. 없으면(구 서버) conflicts 문자열을 전부 미확인으로 본다. */
export function conflictsOf(p: SVProposal): { status: SVConflictStatus[]; unacknowledged: number[] } {
  if (Array.isArray(p.conflicts_status)) {
    const status = p.conflicts_status.filter(c => c && typeof c.index === 'number');
    const unack = Array.isArray(p.conflicts_unacknowledged)
      ? p.conflicts_unacknowledged.filter(n => typeof n === 'number')
      : status.filter(c => !c.acknowledged).map(c => c.index);
    return { status, unacknowledged: unack };
  }
  const status = (p.conflicts ?? []).map((c, i) => ({
    index: i,
    text: typeof c === 'string' ? c : JSON.stringify(c),
    acknowledged: false,
    stale: false,
  }));
  return { status, unacknowledged: status.map(c => c.index) };
}

export function evidenceLevelOf(e: SVProposalEvidence): string {
  return (e.evidence_level || e.level || '').toString().trim() || '—';
}

/**
 * 근거 기준 **참고 표시** (승인 판정 아님 — 최종 판정은 서버 approval.plan_approval).
 * approval.has_evidence 와 같은 규칙으로 기준 충족 여부를 미리 보여준다:
 *   source_expansion: 공식 출처군이면 공식 E3+, 아니면 E2+ / role_change: 공식 E3+ / new_person: E2+ ;
 *   인정 항목은 publisher·locator 가 모두 있어야 한다.
 */
export function evidenceGate(p: SVProposal): { required: string; met: boolean; summary: string } {
  const ev = (p.evidence ?? []).filter((e): e is SVProposalEvidence => !!e && typeof e === 'object');
  const counts: Record<string, number> = {};
  ev.forEach(e => {
    const l = evidenceLevelOf(e);
    counts[l] = (counts[l] ?? 0) + 1;
  });
  const summary = Object.keys(counts).length
    ? Object.entries(counts)
        .sort(([a], [b]) => b.localeCompare(a))
        .map(([l, n]) => `${l}×${n}`)
        .join(' · ')
    : '근거 없음';
  const group = String((p.proposed_change ?? {})['source_group'] ?? '');
  let levels = ['E2', 'E3', 'E4'];
  let official = false;
  if (p.proposal_type === 'role_change' || (p.proposal_type === 'source_expansion' && group === 'official')) {
    levels = ['E3', 'E4'];
    official = true;
  }
  const met = ev.some(
    e =>
      levels.includes(evidenceLevelOf(e)) &&
      (!official || e.source_class === 'official') &&
      !!e.publisher &&
      !!e.locator,
  );
  return { required: official ? '공식 E3 이상' : 'E2 이상', met, summary };
}

export function adminListProposals(params: { status?: string; type?: string } = {}): Promise<SVProposalList> {
  return svRequest<SVProposalList>('GET', `/api/admin/stakeholder/proposals${qs(params)}`).then(r => ({
    ...r,
    proposals: r?.proposals ?? [],
  }));
}

/** 사이드바 배지 — 열린 제안(needs_review + needs_evidence) 수. admin 전용 호출, 실패 시 0(배지 없음). */
export async function fetchPendingProposalCount(): Promise<number> {
  try {
    const r = await adminListProposals();
    const open = (r.proposals ?? []).filter(p =>
      (OPEN_PROPOSAL_STATUSES as readonly string[]).includes(p.status ?? ''),
    ).length;
    const n = r.proposals.length ? open : (r.pending_count ?? 0);
    return Number.isFinite(n) && n > 0 ? n : 0;
  } catch {
    return 0;
  }
}

export function adminApprove(id: string, reason: string): Promise<SVProposal> {
  return svRequest<SVProposal>('POST', `/api/admin/stakeholder/proposals/${encodeURIComponent(id)}/approve`, {
    reason,
  });
}

export function adminReject(id: string, reason: string): Promise<SVProposal> {
  return svRequest<SVProposal>('POST', `/api/admin/stakeholder/proposals/${encodeURIComponent(id)}/reject`, {
    reason,
  });
}

/** 충돌 1건 확인(T2b). index 는 0-based. 200 → proposal_view(제안 + conflicts_status + conflicts_unacknowledged). */
export function adminAckConflict(id: string, index: number, reason: string): Promise<SVProposal> {
  return svRequest<SVProposal>(
    'POST',
    `/api/admin/stakeholder/proposals/${encodeURIComponent(id)}/conflicts/${index}/ack`,
    { reason },
  );
}

export async function adminListConflictAcks(id: string): Promise<SVConflictAck[]> {
  const r = await svRequest<{ items?: SVConflictAck[] }>(
    'GET',
    `/api/admin/stakeholder/proposals/${encodeURIComponent(id)}/conflict-acks`,
  );
  return r?.items ?? [];
}

/** 관리 목록 — 같은 GET /api/stakeholder/people (admin 토큰이면 서버가 admin 필드를 더한다). */
export function adminListPeople(params: { status?: string; q?: string } = {}): Promise<SVPeopleList> {
  return fetchPeople(params);
}

export function adminCreatePerson(input: SVPersonInput): Promise<SVPersonDetail> {
  return svRequest<SVPersonDetail>('POST', '/api/admin/stakeholder/people', input);
}

export function adminUpdatePerson(id: string, input: SVPersonInput): Promise<SVPersonDetail> {
  return svRequest<SVPersonDetail>('PUT', `/api/admin/stakeholder/people/${encodeURIComponent(id)}`, input);
}

export function adminSetStatus(id: string, status: 'active' | 'paused'): Promise<SVPersonDetail> {
  return svRequest<SVPersonDetail>('PATCH', `/api/admin/stakeholder/people/${encodeURIComponent(id)}/status`, {
    status,
  });
}

/** multipart `photo`. 서버가 WEBP 로 정규화. 415/413 은 서버 문구 그대로 SVApiError. */
export async function adminUploadPhoto(id: string, file: File): Promise<SVPersonDetail> {
  const fd = new FormData();
  fd.append('photo', file);
  const r = await svRequest<SVPersonDetail>(
    'POST',
    `/api/admin/stakeholder/people/${encodeURIComponent(id)}/photo`,
    fd,
  );
  photoCache.delete(id);
  return r;
}

export async function adminDeletePhoto(id: string): Promise<SVPersonDetail> {
  const r = await svRequest<SVPersonDetail>('DELETE', `/api/admin/stakeholder/people/${encodeURIComponent(id)}/photo`);
  photoCache.delete(id);
  return r;
}

export async function adminSourceDomains(): Promise<{ effective_count?: number; items: SVSourceDomain[] }> {
  const r = await svRequest<{ effective_count?: number; items?: SVSourceDomain[] }>(
    'GET',
    '/api/admin/stakeholder/source-domains',
  );
  return { ...r, items: r?.items ?? [] };
}

/** 실제 T6 응답: sv_meta 요약 + 상태별 개수 + 문제(mismatch/missing) 행. sources_total 은 no_local 제외(서버 정의). */
export interface SVIntegrityProblem {
  source_id?: string;
  path?: string;
  name?: string | null;
  title?: string | null;
  integrity_status?: string | null;
  integrity_checked_at?: string | null;
  local_path?: string | null;
  text_path?: string | null;
  sha256?: string | null;
  text_sha256?: string | null;
}

export interface SVIntegrityReport {
  /** sv_meta.integrity_summary_json (sync 소유 — 모양 가변) */
  summary?: Record<string, unknown> | null;
  last_sync_at?: string | null;
  /** sv_source integrity_status → 개수 */
  counts?: Record<string, number> | null;
  /** sv_artifact integrity_status → 개수 */
  artifact_counts?: Record<string, number> | null;
  problems?: SVIntegrityProblem[] | null;
  artifact_problems?: SVIntegrityProblem[] | null;
}

export function adminIntegrity(): Promise<SVIntegrityReport> {
  return svRequest<SVIntegrityReport>('GET', '/api/admin/stakeholder/integrity');
}

/** runbooks.run("stakeholder_data_sync") 결과. T8 전에는 503 {code:'RUNBOOK_MISSING'}. */
export function adminSync(): Promise<Record<string, unknown>> {
  return svRequest<Record<string, unknown>>('POST', '/api/admin/stakeholder/sync');
}
