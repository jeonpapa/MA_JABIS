import { api } from './client';

// ── 상수 (백엔드 agents/reimb_pipeline.py 와 동기) ──────────────────────────

export const QUEUE_STATES = [
  'QUEUE_PENDING',
  'QUEUE_PROCESSED',
  'APPROVED',
  'REJECTED_REQUEUE',
  'WITHDRAWN',
] as const;
export type QueueState = (typeof QUEUE_STATES)[number];

export const QUEUE_STATE_LABEL: Record<QueueState, string> = {
  QUEUE_PENDING: '대기',
  QUEUE_PROCESSED: '심의 완료',
  APPROVED: '통과',
  REJECTED_REQUEUE: '재심의',
  WITHDRAWN: '철회',
};

export const NEGOTIATION_STATUSES = [
  'NONE',
  'IN_PROGRESS',
  'STALLED',
  'AGREED',
  'REJECTED',
] as const;
export type NegotiationStatus = (typeof NEGOTIATION_STATUSES)[number];

export const NEGOTIATION_LABEL: Record<NegotiationStatus, string> = {
  NONE: '없음',
  IN_PROGRESS: '진행 중',
  STALLED: '교착',
  AGREED: '타결',
  REJECTED: '결렬',
};

export const TRACKING_PRIORITIES = [
  'msd_asset',
  'competitor_class',
  'generic_new_drug',
] as const;
export type TrackingPriority = (typeof TRACKING_PRIORITIES)[number];

export const TRACKING_PRIORITY_LABEL: Record<TrackingPriority, string> = {
  msd_asset: 'MSD 자산',
  competitor_class: '경쟁 계열',
  generic_new_drug: '일반 신약',
};

export type Committee = 'cancer' | 'evaluation';
export const COMMITTEE_LABEL: Record<Committee, string> = {
  cancer: '암질심',
  evaluation: '약평위',
};

export type Stage = 'cancer' | 'evaluation' | 'nhis';
export const STAGE_LABEL: Record<Stage, string> = {
  cancer: '암질심',
  evaluation: '약평위',
  nhis: '건보협상',
};

export type ListingType = '신규' | '확대';

// ── 타입 ─────────────────────────────────────────────────────────────────────

/** events[] 항목 (백엔드 _history() 직렬화) */
export interface QueueEvent {
  id: number;
  date: string | null;
  committee: string; // '암질심' | '약평위' (KR label)
  state: QueueState;
  stateLabel: string;
  sessionId: number | null;
  sessionDate: string | null;
  cycle: number | null;
  year?: number | null;
  cycleLabel?: string | null;   // '2025년 9차'
  attempt: number | null;
  evidenceUrl: string | null;   // http(s) 링크일 때만
  evidenceText?: string | null; // 링크가 아닌 근거 메모
  observedAt?: string | null;
  roundId?: number | null;          // 이 브랜드의 등재 차수 링크 (없으면 미연결 — 조회 시 규칙 배정)
  linkedRoundIds?: number[];        // 병용 공유 포함 전체 차수 링크
  drugId?: number;                  // shared_events: 이벤트 소유 브랜드
}

/** 등재 차수(listing round) — 브랜드 1 : N. round_id null = 분할 전 가상 차수(약제 행 그대로) */
export interface ComboPartner {
  drugId: number;
  name: string | null;
  roundId: number;
  roundLabel: string;
}

export interface AdminRound {
  round_id: number | null;
  round_label: string | null;       // '2026 확대 · 파드셉 병용 요로상피암 1차'
  list_type: ListingType | null;
  indication_scope: string | null;
  combo_key: string | null;
  combo_partners: ComboPartner[];
  source: string | null;            // legacy|migration|auto_nhis|auto_committee|admin|committee_import
  needs_review: boolean | null;
  is_primary: boolean;
  submitted_date: string | null;
  amjilsim_pass_date: string | null;
  yakpyungwi_pass_date: string | null;
  negotiation_status: NegotiationStatus | null;
  nhis_registered_ym: string | null;
  negotiation_complete_date: string | null;
  negotiation_date_source: string | null;
  reimb_start_date: string | null;
  notes: string | null;
  stage: Stage;
  status: PipelineStatus;
  next_step: string;
  last_result: AdminLastResult | null;
  event_ids: number[];
  shared_events: QueueEvent[];      // 다른 브랜드 이벤트 (병용 공유 링크)
  nhis_ids: number[];
}

/** 브랜드의 공단 협상 행 (차수 연결 UI) */
export interface AdminNhisRow {
  id: number;
  listType: ListingType;
  productName: string;
  registeredYm: string | null;
  result: string | null;
  completedYm: string | null;
  inProgress: boolean;
  superseded: boolean;              // 완료 행으로 대체된 협상 중 아카이브
  roundId: number | null;
  roundLabel: string | null;
  roundLinkSource: string | null;
}

export interface RoundPayload {
  round_label?: string;
  list_type?: ListingType | null;
  indication_scope?: string | null;
  combo_key?: string | null;
  submitted_date?: string | null;
  amjilsim_pass_date?: string | null;
  yakpyungwi_pass_date?: string | null;
  negotiation_status?: NegotiationStatus | null;
  nhis_registered_ym?: string | null;
  negotiation_complete_date?: string | null;
  reimb_start_date?: string | null;
  notes?: string | null;
  needs_review?: boolean;
  event_ids?: number[];
  nhis_ids?: number[];
}

/** 마지막 결과 (백엔드 reimb_pipeline.last_result) — 위원회 이벤트·공단 협상 중 사건일이 가장 늦은 것 */
export interface AdminLastResult {
  kind: 'committee' | 'negotiation';
  committee: Committee | null;
  committeeLabel: string;
  state: string;
  label: string;
  date: string | null;
  month?: string | null;
  sessionLinked: boolean;
  cycleLabel: string | null;
  source: string;
}

export type PipelineStatus = 'completed' | 'negotiating' | 'scheduled' | 'waiting';
export const PIPELINE_STATUS_LABEL: Record<PipelineStatus, string> = {
  waiting: '심의 대기',
  scheduled: '상정 예정',
  negotiating: '협상 중',
  completed: '협상 완료',
};

export interface LatestQueue {
  id: number;
  committee: Committee | null;
  state: QueueState;
  session_id: number | null;
  session_date: string | null;
  queue_entry_date: string | null;
  attempt: number | null;
  evidence_url: string | null;
  observed_at: string | null;
  event_date?: string | null; // 사건일 (회의일 → 큐 등재일 → 관측일) — '최근' 판정 기준
}

/** GET /api/admin/reimb-pipeline/drugs → items[] (raw snake_case + 계산 필드) */
export interface AdminDrug {
  drug_id: number;
  brand_kr: string;
  brand_en: string | null;
  ingredient_inn: string | null;
  atc: string | null;
  manufacturer: string | null;
  product_slug: string | null;
  competitor_class: string | null;
  msd_flag: boolean;
  tracking_priority: TrackingPriority | null;
  indication: string | null;
  listing_type: ListingType | null;
  submitted_date: string | null;
  notes: string | null;
  amjilsim_pass_date: string | null;
  yakpyungwi_pass_date: string | null;
  negotiation_status: NegotiationStatus | null;
  first_seen_at?: string | null;
  stage: Stage;
  status?: PipelineStatus;
  next_step?: string;              // '암질심 심의' | '약평위 심의' | '건보공단 협상' | '협상 완료'
  last_result?: AdminLastResult | null;
  updated_date?: string | null;
  stale_days?: number | null;
  is_stale?: boolean;
  key_issues_display?: string[];
  unlinked_events?: number;        // 회의 차수에 연결되지 않은 이벤트 수
  negotiation_complete_date?: string | null;
  negotiation_date_source?: string | null;
  latest_queue: LatestQueue | null;
  events: QueueEvent[];
  round_label?: string | null;      // 대표 차수 라벨
  round_count?: number;             // 실제 차수 수 (0 = 분할 전)
  rounds?: AdminRound[];
  nhis_rows?: AdminNhisRow[];
}

export interface DrugPayload {
  brand_kr?: string;
  brand_en?: string | null;
  ingredient_inn?: string | null;
  manufacturer?: string | null;
  indication?: string | null;
  listing_type?: ListingType | null;
  submitted_date?: string | null;
  msd_flag?: boolean;
  tracking_priority?: TrackingPriority | null;
  notes?: string | null;
  amjilsim_pass_date?: string | null;
  yakpyungwi_pass_date?: string | null;
  negotiation_status?: NegotiationStatus | null;
}

export interface EventPayload {
  committee: Committee;
  state: QueueState;
  session_id?: number | null;
  queue_entry_date?: string | null;
  attempt?: number;
  evidence_url?: string | null;
  round_id?: number | null;         // 등재 차수 명시 연결
}

/** GET /api/reimbursement/meetings → items[] */
export interface Meeting {
  id: number;
  committee: Committee;
  year: number;
  cycle: number | null;
  date: string | null;
  status: string | null; // 'SCHEDULED' | 'COMPLETED'
  note: string | null;
  minutes_url: string | null;
}

export interface SessionPatch {
  status?: string;
  ordinal_official?: number | null;
  note?: string | null;
  official_minutes_url?: string | null;
}

export interface SessionCreate {
  committee: Committee;
  year: number;
  ordinal_assumed: number;
  session_date: string;
  note?: string | null;
}

// ── API ──────────────────────────────────────────────────────────────────────

const BASE = '/api/admin/reimb-pipeline';

export async function listAdminDrugs(): Promise<AdminDrug[]> {
  const r = await api.get<{ items: AdminDrug[] }>(`${BASE}/drugs`);
  return r.items;
}

export async function createDrug(payload: DrugPayload): Promise<{ drug_id: number }> {
  return api.post<{ drug_id: number }>(`${BASE}/drugs`, payload);
}

export async function updateDrug(
  drugId: number,
  payload: DrugPayload,
): Promise<{ drug_id: number; updated_fields: number }> {
  return api.patch<{ drug_id: number; updated_fields: number }>(`${BASE}/drugs/${drugId}`, payload);
}

export async function deleteDrug(
  drugId: number,
): Promise<{ drug_id: number; deleted_events: number }> {
  return api.delete<{ drug_id: number; deleted_events: number }>(`${BASE}/drugs/${drugId}`);
}

export async function addDrugEvent(
  drugId: number,
  payload: EventPayload,
): Promise<{ event_id: number; drug_id: number }> {
  return api.post<{ event_id: number; drug_id: number }>(`${BASE}/drugs/${drugId}/events`, payload);
}

export async function deleteDrugEvent(eventId: number): Promise<{ event_id: number }> {
  return api.delete<{ event_id: number }>(`${BASE}/events/${eventId}`);
}

export async function listMeetings(): Promise<Meeting[]> {
  const r = await api.get<{ items: Meeting[] }>('/api/reimbursement/meetings');
  return r.items;
}

export async function updateSession(
  sessionId: number,
  patch: SessionPatch,
): Promise<{ session_id: number; updated_fields: number }> {
  return api.patch<{ session_id: number; updated_fields: number }>(
    `${BASE}/sessions/${sessionId}`,
    patch,
  );
}

export async function createSession(payload: SessionCreate): Promise<{ session_id: number }> {
  return api.post<{ session_id: number }>(`${BASE}/sessions`, payload);
}

// ── NHIS 미매칭 audit (수동 등록 검토 대상) ─────────────────────────────────────

/** 공단 공개자료 중 amjilsim_drugs 와 매칭 안 된 행 (drug_id NULL). */
export interface NhisUnmatched {
  id: number;
  listType: '신규' | '확대';
  productName: string;
  manufacturer: string | null;
  efficacyGroup: string | null;
  registeredYm: string | null;
  result: string | null;
  completedYm: string | null;
  inProgress: boolean;
  matched: boolean;
  drugId: number | null;
  sourceUrl: string;
  fetchedAt: string | null;
}

export async function listNhisUnmatched(): Promise<{ items: NhisUnmatched[]; count: number }> {
  return api.get<{ items: NhisUnmatched[]; count: number }>(`${BASE}/nhis-unmatched`);
}

// ── 등재 차수(listing round) ───────────────────────────────────────────────────

export async function createRound(
  drugId: number,
  payload: RoundPayload,
): Promise<{ round_id: number; drug_id: number; default_round_id: number | null }> {
  return api.post(`${BASE}/drugs/${drugId}/rounds`, payload);
}

export async function updateRound(
  roundId: number,
  payload: RoundPayload,
): Promise<{ round_id: number; updated_fields: number }> {
  return api.patch(`${BASE}/rounds/${roundId}`, payload);
}

export async function deleteRound(
  roundId: number,
): Promise<{ round_id: number; unlinked_events: number; unlinked_nhis: number }> {
  return api.delete(`${BASE}/rounds/${roundId}`);
}

/** 이벤트 → 차수 명시 연결 (같은 브랜드 다른 차수 링크는 교체, 다른 브랜드 이벤트 = 병용 공유 링크) */
export async function linkEventToRound(roundId: number, eventId: number): Promise<{ round_id: number; event_id: number }> {
  return api.post(`${BASE}/rounds/${roundId}/events`, { event_id: eventId });
}

export async function unlinkEventFromRound(roundId: number, eventId: number): Promise<{ round_id: number; event_id: number }> {
  return api.delete(`${BASE}/rounds/${roundId}/events/${eventId}`);
}

/** 공단 협상 행(+같은 협상 형제 행) → 차수 연결 / null = 해제 */
export async function setNhisRound(
  nhisId: number,
  roundId: number | null,
): Promise<{ nhis_id: number; round_id: number | null; rows: number[] }> {
  return api.put(`${BASE}/nhis/${nhisId}/round`, { round_id: roundId });
}
