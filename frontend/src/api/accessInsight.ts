// Access Insight (Phase 4 S3) — 약제별 미디어 signal momentum + journey overlay.
//
// 백엔드 집계 로직: agents/access_insight/aggregate.py (drug_momentum / leaderboard /
// journey / list_drugs_with_signals). 라우트: api/server.py `/api/access-insight/*`
// (전부 @require_auth() — 로그인 사용자 누구나 조회 가능, admin 전용 아님).
//
// momentum_score 는 참고 신호(likelihood signal)이며 확정 예측이 아니다 — UI 문구에서도
// 이 점을 항상 명시한다.

import { api } from './client';

/** amjilsim_media_signals.signal_type CHECK enum 6종 (QUEUE_INVENTORY 는 S5 전용이라 제외). */
export type SignalType =
  | 'GOV_STATEMENT'
  | 'PATIENT_PETITION'
  | 'KOL_OPINION'
  | 'IR_RELEASE'
  | 'RESULT_REPORT'
  | 'PRE_AGENDA_LEAK';

export const SIGNAL_TYPES: SignalType[] = [
  'GOV_STATEMENT',
  'PATIENT_PETITION',
  'KOL_OPINION',
  'IR_RELEASE',
  'RESULT_REPORT',
  'PRE_AGENDA_LEAK',
];

export interface ExpectedSession {
  session_id: number;
  session_date: string;
  committee_type: string;
  status: string;
}

export interface MomentumTrend {
  recent_30d: number;
  prior_30d: number;
  direction: 'up' | 'down' | 'flat';
}

/** aggregate.py::drug_momentum() 반환 shape. `session_imminent` 은 leaderboard() 가 후처리로 추가. */
export interface DrugMomentum {
  drug_id: number;
  brand_kr: string;
  product_slug: string | null;
  reference_date: string | null;
  expected_session: ExpectedSession | null;
  window_days: number;
  signal_count: number;
  weighted_sum: number;
  momentum_score: number;
  by_type: Record<SignalType, number>;
  engage_diversity: number;
  trend: MomentumTrend;
  session_imminent?: boolean;
}

export interface DrugListItem {
  drug_id: number;
  brand_kr: string;
  signal_count: number;
}

export interface JourneySignal {
  published_at: string;
  signal_type: SignalType | string;
  title: string;
  url: string | null;
  weight: number;
  outlet: string | null;
}

export interface JourneySession {
  session_id: number;
  session_date: string;
  committee_type: string;
  ordinal: number | null;
  status: string;
}

export interface JourneyMilestones {
  amjilsim_pass_date: string | null;
  yakpyungwi_pass_date: string | null;
  mfds_permit_date: string | null;
  first_reimbursement_date: string | null;
  reimbursement_effective_date: string | null;
}

export interface DrugJourney {
  drug_id: number;
  brand_kr: string;
  product_slug: string | null;
  signals: JourneySignal[];
  sessions: JourneySession[];
  milestones: JourneyMilestones;
}

export interface DrugJourneyResponse {
  momentum: DrugMomentum;
  journey: DrugJourney;
}

export async function fetchAccessLeaderboard(windowDays = 90, limit = 30): Promise<DrugMomentum[]> {
  const params = new URLSearchParams({ window_days: String(windowDays), limit: String(limit) });
  const r = await api.get<{ items: DrugMomentum[] }>(`/api/access-insight/leaderboard?${params.toString()}`);
  return r.items;
}

export async function fetchAccessDrugs(): Promise<DrugListItem[]> {
  const r = await api.get<{ items: DrugListItem[] }>('/api/access-insight/drugs');
  return r.items;
}

export async function fetchAccessDrugJourney(drugId: number, windowDays = 90): Promise<DrugJourneyResponse> {
  return api.get<DrugJourneyResponse>(`/api/access-insight/drug/${drugId}?window_days=${windowDays}`);
}
