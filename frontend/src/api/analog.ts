// 약제 등재 아날로그 검색 (Listing Analog Search)
// DREC Raw PDF 651건 — 질환분류·효과지표·정책의도·타임라인 포함
import { api } from './client';

export interface AnalogFacetValue { value: string; count: number; }
export type AnalogFacets = Record<string, AnalogFacetValue[]>;

export interface EfficacyEndpoint {
  trial_name: string | null;
  endpoint: string;
  endpoint_ko: string | null;
  endpoint_detail: string | null;
  value: number | null;
  value_unit: string | null;
  comparator_name: string | null;
  comparator_value: number | null;
  hr: number | null;
  ci_lower: number | null;
  ci_upper: number | null;
  p_value: string | null;
  n: number | null;
  note: string | null;
}

export interface CommitteeEvent {
  type: string;        // 약평위 | 암질심
  date: string;
  ordinal: number | null;
  result: string | null;
}

export interface AnalogReport {
  id: number;
  file_name: string;
  // 식별
  brand_name: string | null;
  brand_name_raw: string | null;
  dosage: string | null;            // 용량/강도 (brand_name 에서 분리)
  generic_name: string | null;
  generic_name_en: string | null;
  manufacturer: string | null;
  session_year: number | null;
  ordinal: number | null;
  pdf_extractable: number | null;   // 1=정상, 0=스캔PDF
  // 질환 분류
  disease_category: string | null;         // 항암/비항암/희귀
  disease_category_detail: string | null;  // 혈액종양/고형암/...
  disease_name: string | null;             // 기존 호환
  disease_name_ko: string | null;          // 비소세포폐암
  disease_name_en: string | null;          // NSCLC
  cancer_type: string | null;
  line_of_therapy: string | null;
  biomarker: string | null;
  treatment_setting: string | null;
  // 결정
  committee: string | null;
  session_date: string | null;
  review_result: string | null;            // APPROVED/REJECTED/...
  review_result_ko: string | null;
  post_url?: string | null;                // 약평위 HIRA 게시물 링크(메타 매칭)
  result_meta?: string | null;             // 메타 원문 결과(급여/비급여/조건부)
  post_blt_no?: number | null;
  first_reimbursement_date?: string | null;  // 국내약가 최초 등재일(급여 등재)
  // RSA/사후조건 미디어 보완(별도 — PDF 원본과 분리)
  rsa_media_conditions?: string | null;    // JSON 구체 조건
  rsa_media_monitoring?: string | null;    // JSON 사후 모니터링
  rsa_media_sources?: string | null;       // JSON [{title,url,media,date}]
  rsa_media_confidence?: string | null;
  reimbursement_track: string | null;      // 기존 호환
  reimbursement_track_ko: string | null;   // 한국어 트랙
  has_rsa: number | null;
  pe_waiver: number | null;
  has_postmarket_condition: number | null;
  postmarket_condition_detail: string | null;
  rsa_type_hint: string | null;
  rsa_types: string[];                     // 기존 호환
  // 히스토리
  committee_history: CommitteeEvent[];
  amjilsim_history: { date: string; committee?: string }[];
  first_session_date: string | null;
  pass_session_date: string | null;
  sessions_to_pass: number | null;
  requeue_count: number | null;
  days_mfds_to_first_committee: number | null;
  // 효과 지표
  efficacy_data: EfficacyEndpoint[];
  primary_endpoint: string | null;
  os_months: number | null;
  pfs_months: number | null;
  orr_pct: number | null;
  key_hr: number | null;
  comparator_drugs: string[];
  clinical_trials: string[];
  // 해외 등재
  foreign_listing_count: number | null;
  foreign_listing_basis: number | null;    // 7 or 8
  consulted_societies: string[];           // 의견조회 학회
  medical_necessity: string | null;
  // 정책
  policy_signals: string[];
  policy_intent_summary: string | null;
  policy_tags: string[];
  approval_driver: string | null;
  future_conditions: string | null;
  policy_drivers: string[];                // 기존 호환
  // 허가↔급여 갭
  mfds_permit_date: string | null;
  mfds_approval_date: string | null;
  mfds_effect_text?: string | null;        // detail 에서만
  coverage_gap_type: string | null;
  coverage_gap_evidence: string | null;
  lag_days_approval_to_reimb: number | null;
  // 기타
  wikilinks: string[];
  body_text?: string;                      // detail 에서만
  decision_reason?: string | null;         // detail 에서만
  similarity?: number;
}

export interface MatchedConcept {
  concept_id: string;
  type: string | null;
  canonical_ko: string | null;
  canonical_en: string | null;
  matched_via: string;
}

export interface AnalogQueryGroup {
  label: string;
  kind: 'concept' | 'field';
  domain_key: string | null;
  concept_ids: string[];
}

export interface AnalogQueryDebug {
  matched_concepts: MatchedConcept[];
  concept_count: number;
  tag_rerank: boolean;
  and_rerank?: boolean;          // 공백 구분 2개 이상 조건 = AND 결합
  groups?: AnalogQueryGroup[];   // AND 그룹 (조건별)
}

export interface AnalogSearchResult {
  mode: 'facet' | 'search';
  count: number;
  results: AnalogReport[];
  query_debug?: AnalogQueryDebug;   // 동의어 인지: 인식된 concept (칩 표시용)
}

export interface AnalogBrief {
  brief: string;
  cached?: boolean;
  cited_ids: number[];
  error?: string;
  cases?: { id: number; brand_name: string | null; session_date: string | null }[];
}

export const FACET_LABELS: Record<string, string> = {
  disease_category: '질환군',
  disease_category_detail: '세부질환군',
  cancer_type: '암종',
  line_of_therapy: '치료차수',
  committee: '위원회',
  review_result: '심의결과',
  reimbursement_track_ko: '등재트랙',
  coverage_gap_type: '허가↔급여 갭',
  medical_necessity: '의료적필요성',
  approval_driver: '등재동인',
};

export const REVIEW_RESULT_KO: Record<string, string> = {
  APPROVED: '급여 적정',
  CONDITIONAL_APPROVED: '조건부 급여',
  APPROVED_WITH_POSTMARKET: '사후관리 조건부 급여',
  REJECTED: '급여 불인정',
  UNKNOWN: '미확인',
};

export function fetchAnalogFacets(filters?: Record<string, string>): Promise<AnalogFacets> {
  const qs = new URLSearchParams();
  Object.entries(filters ?? {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') qs.set(k, String(v));
  });
  const suffix = qs.toString();
  return api.get<AnalogFacets>(`/api/analog/facets${suffix ? `?${suffix}` : ''}`);
}

export function searchAnalog(params: {
  filters?: Record<string, string | number>;
  q?: string;
  limit?: number;
}): Promise<AnalogSearchResult> {
  const qs = new URLSearchParams();
  Object.entries(params.filters ?? {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') qs.set(k, String(v));
  });
  if (params.q) qs.set('q', params.q);
  qs.set('limit', String(params.limit ?? 50));
  return api.get<AnalogSearchResult>(`/api/analog/search?${qs.toString()}`);
}

export function fetchAnalogDetail(id: number): Promise<AnalogReport> {
  return api.get<AnalogReport>(`/api/analog/report/${id}`);
}

export function generateAnalogBrief(reportIds: number[], query: string): Promise<AnalogBrief> {
  return api.post<AnalogBrief>('/api/analog/brief', { report_ids: reportIds, query });
}

// 검색어 피드백: 결과가 의도와 다를 때 사용자가 실제 찾던 약제를 남김
export interface SearchFeedbackInput {
  query: string;                       // 입력했던 검색어
  filters: Record<string, string>;     // 검색 당시 드롭다운 필터
  returned_ids: number[];              // 노출됐던 상위 결과 id
  returned_top?: string;               // 상위 결과 브랜드명 요약
  intended_text: string;               // 실제 찾고자 했던 약제/내용
  note?: string;                       // 추가 코멘트
}

export function submitSearchFeedback(input: SearchFeedbackInput): Promise<{ ok: boolean; id: number }> {
  return api.post<{ ok: boolean; id: number }>('/api/analog/search-feedback', input);
}
