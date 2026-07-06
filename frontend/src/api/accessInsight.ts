// Access Insight (Phase 4 S3) — 약제별 미디어 signal momentum + journey overlay.
//
// 백엔드 집계 로직: agents/access_insight/aggregate.py (drug_momentum / leaderboard /
// journey / list_drugs_with_signals). 라우트: api/server.py `/api/access-insight/*`
// (전부 @require_auth() — 로그인 사용자 누구나 조회 가능, admin 전용 아님).
//
// momentum_score 는 참고 신호(likelihood signal)이며 확정 예측이 아니다 — UI 문구에서도
// 이 점을 항상 명시한다.

import { api } from './client';

/**
 * amjilsim_media_signals.signal_type CHECK enum (QUEUE_INVENTORY 는 S5 전용이라 제외).
 * UNCLASSIFIED 는 B7 재분류로 추가된 저신뢰 미분류 버킷.
 */
export type SignalType =
  | 'GOV_STATEMENT'
  | 'PATIENT_PETITION'
  | 'KOL_OPINION'
  | 'IR_RELEASE'
  | 'RESULT_REPORT'
  | 'PRE_AGENDA_LEAK'
  | 'UNCLASSIFIED';

export const SIGNAL_TYPES: SignalType[] = [
  'GOV_STATEMENT',
  'PATIENT_PETITION',
  'KOL_OPINION',
  'IR_RELEASE',
  'RESULT_REPORT',
  'PRE_AGENDA_LEAK',
  'UNCLASSIFIED',
];

/** 항암/일반 약제 필터 (B6). 백엔드 `?class=oncology|general`. */
export type DrugClass = 'oncology' | 'general';

/**
 * 위원회 DB enum → 사용자 표기 (B4, display-only — DB enum 값은 불변).
 * AMJILSIM=암질심(DREC), YAKPYUNGWI=약평위(ODAC), BENEFIT_SUBCOMMITTEE=급여기준소위(BSC).
 */
export const COMMITTEE_LABELS: Record<string, string> = {
  AMJILSIM: 'DREC',
  YAKPYUNGWI: 'ODAC',
  BENEFIT_SUBCOMMITTEE: 'BSC',
};

/**
 * 위원회 코드 → 표기. 3개 실제 enum(AMJILSIM/YAKPYUNGWI/BENEFIT_SUBCOMMITTEE)만
 * DREC/ODAC/BSC 로 매핑. null/undefined/'UNKNOWN'(항암 분류 미상 — 백필 전이거나
 * 분류 불가) 이면 '' 을 반환해 호출부가 라벨을 조건부로 숨기게 한다 (잘못된 BSC 방지).
 */
export function committeeLabel(code: string | null | undefined): string {
  if (!code || code === 'UNKNOWN') return '';
  return COMMITTEE_LABELS[code] ?? code;
}

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
  /** 1=항암, 0=일반, null=미분류(전체 필터에서만 노출). */
  is_oncology: 1 | 0 | null;
  /**
   * 예상 진입 위원회 (AMJILSIM | BENEFIT_SUBCOMMITTEE) — COMMITTEE_LABELS 로 표기.
   * 항암 분류 미상이면 null | 'UNKNOWN' — 이 경우 라벨을 숨긴다.
   */
  expected_committee: string | null;
}

export interface DrugListItem {
  drug_id: number;
  brand_kr: string;
  signal_count: number;
  is_oncology: 1 | 0 | null;
  expected_committee?: string;
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

export async function fetchAccessLeaderboard(
  windowDays = 90,
  limit = 30,
  drugClass?: DrugClass,
): Promise<DrugMomentum[]> {
  const params = new URLSearchParams({ window_days: String(windowDays), limit: String(limit) });
  if (drugClass) params.set('class', drugClass); // B6 — 항암/일반 서버측 필터
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
