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
 * 신호 노출 강도 (A1). title=제목 직접 언급, body_strong=본문 실질 언급,
 * passing=간접·라운드업성 스치는 언급. 구버전 데이터에는 필드 자체가 없을 수 있음
 * — 그 경우 실질 신호(substantive)로 취급한다.
 */
export type Prominence = 'title' | 'body_strong' | 'passing';

/** 급여 진입 트랙 (A2). oncology=암질심 경유, general=약평위 직행, unknown=분류 미상. */
export type Track = 'oncology' | 'general' | 'unknown';

/** journey 스테이지 스텝퍼 항목 (A2). 백엔드 stages[] 그대로. */
export interface StageItem {
  key: string;
  label: string;
  date: string | null;
  status: 'done' | 'current' | 'pending';
  /**
   * 예정(scheduled) 마커 — current 스테이지가 확정 통과일이 아닌 *예정된* 위원회
   * 세션(예: 림카토주 암질심 예정)일 때 true, date 는 예정 세션일.
   * 구버전 응답에는 필드 자체가 없음(그 경우 기존 렌더링 그대로).
   */
  scheduled?: boolean;
}

/** momentum 점수 구간 경계 (A3). 백엔드 score_bands. 없으면 DEFAULT_SCORE_BANDS 폴백. */
export interface ScoreBands {
  high: number;
  medium: number;
}

export const DEFAULT_SCORE_BANDS: ScoreBands = { high: 3, medium: 1 };

/**
 * 위원회 DB enum → 사용자 표기 (display-only — DB enum 값은 불변).
 * A2: DREC/ODAC/BSC 영문 약칭을 폐기하고 정확한 한국어 명칭으로 교체.
 * BENEFIT_SUBCOMMITTEE 는 백엔드 계약에서 제거됨 — 매핑하지 않는다(구버전 값이 오면 숨김).
 */
export const COMMITTEE_LABELS: Record<string, string> = {
  AMJILSIM: '암질심',
  YAKPYUNGWI: '약평위',
};

/**
 * 위원회 코드 → 표기. AMJILSIM/YAKPYUNGWI 만 한국어 라벨로 매핑.
 * null/undefined/'UNKNOWN'/미지 코드(예: 구버전 BENEFIT_SUBCOMMITTEE)는 '' 을 반환해
 * 호출부가 라벨을 조건부로 숨기게 한다.
 */
export function committeeLabel(code: string | null | undefined): string {
  if (!code || code === 'UNKNOWN') return '';
  return COMMITTEE_LABELS[code] ?? '';
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
   * 예상 진입 위원회 ('AMJILSIM' | 'YAKPYUNGWI' | null) — COMMITTEE_LABELS 로 표기.
   * 분류 미상이면 null | 'UNKNOWN' — 이 경우 라벨을 숨긴다.
   */
  expected_committee: string | null;
  /** A2 — 급여 진입 트랙. 구버전 응답에는 없을 수 있음(그 경우 배지 숨김). */
  track?: Track | null;
  /** A2 — 스테이지 스텝퍼. 구버전 응답에는 없을 수 있음(그 경우 스텝퍼 숨김). */
  stages?: StageItem[] | null;
  /** A2 — 현재 스테이지 key. */
  current_stage?: string | null;
}

export interface DrugListItem {
  drug_id: number;
  brand_kr: string;
  signal_count: number;
  is_oncology: 1 | 0 | null;
  expected_committee?: string;
  track?: Track | null;
  stages?: StageItem[] | null;
  current_stage?: string | null;
}

export interface JourneySignal {
  published_at: string;
  signal_type: SignalType | string;
  title: string;
  url: string | null;
  weight: number;
  outlet: string | null;
  /** A1 — 노출 강도. 없으면(구버전) 실질 신호로 취급. */
  prominence?: Prominence | string | null;
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
  track?: Track | null;
  stages?: StageItem[] | null;
  current_stage?: string | null;
}

export interface DrugJourneyResponse {
  momentum: DrugMomentum;
  journey: DrugJourney;
  /** A3 — momentum 점수 구간 경계. 없으면 DEFAULT_SCORE_BANDS 폴백. */
  score_bands?: ScoreBands | null;
}

export interface LeaderboardResponse {
  items: DrugMomentum[];
  /** A3 — momentum 점수 구간 경계. 없으면 DEFAULT_SCORE_BANDS 폴백. */
  score_bands?: ScoreBands | null;
}

/** score_bands 응답 필드 방어적 정규화 — 유효 숫자가 아니면 기본 밴드로 폴백. */
export function normalizeScoreBands(raw: ScoreBands | null | undefined): ScoreBands {
  if (
    raw &&
    typeof raw.high === 'number' && Number.isFinite(raw.high) &&
    typeof raw.medium === 'number' && Number.isFinite(raw.medium)
  ) {
    return { high: raw.high, medium: raw.medium };
  }
  return DEFAULT_SCORE_BANDS;
}

export async function fetchAccessLeaderboard(
  windowDays = 90,
  limit = 30,
  drugClass?: DrugClass,
): Promise<LeaderboardResponse> {
  const params = new URLSearchParams({ window_days: String(windowDays), limit: String(limit) });
  if (drugClass) params.set('class', drugClass); // B6 — 항암/일반 서버측 필터
  const r = await api.get<LeaderboardResponse>(`/api/access-insight/leaderboard?${params.toString()}`);
  return { items: r.items ?? [], score_bands: r.score_bands ?? null };
}

export async function fetchAccessDrugs(): Promise<DrugListItem[]> {
  const r = await api.get<{ items: DrugListItem[] }>('/api/access-insight/drugs');
  return r.items;
}

export async function fetchAccessDrugJourney(drugId: number, windowDays = 90): Promise<DrugJourneyResponse> {
  return api.get<DrugJourneyResponse>(`/api/access-insight/drug/${drugId}?window_days=${windowDays}`);
}
