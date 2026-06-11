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
  attempt: number | null;
  evidenceUrl: string | null;
}

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
  latest_queue: LatestQueue | null;
  events: QueueEvent[];
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
