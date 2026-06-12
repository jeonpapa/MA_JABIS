// 약제 등재 아날로그 검색 (Listing Analog Search)
// 537 평가보고서 패싯+FTS+시맨틱 / 허가↔급여 갭 / 재심의 trajectory
import { api } from './client';

export interface AnalogFacetValue { value: string; count: number; }
export type AnalogFacets = Record<string, AnalogFacetValue[]>;

export interface AnalogReport {
  id: number;
  file_name: string;
  title: string | null;
  brand_name: string | null;
  generic_name: string | null;
  manufacturer: string | null;
  disease_category: string | null;
  disease_name: string | null;
  cancer_type: string | null;
  line_of_therapy: string | null;
  committee: string | null;
  session_date: string | null;
  ordinal: number | null;
  review_result: string | null;
  reimbursement_track: string | null;
  rsa_types: string[];
  policy_drivers: string[];
  wikilinks: string[];
  mfds_approval_date: string | null;
  mfds_permit_date: string | null;
  mfds_effect_text?: string | null;       // detail 에서만
  coverage_gap_type: string | null;       // 축소|확대|구체화|동일|비교불가
  coverage_gap_evidence: string | null;
  requeue_count: number | null;
  first_session_date: string | null;
  pass_session_date: string | null;
  sessions_to_pass: number | null;
  lag_days_approval_to_reimb: number | null;
  body_text?: string;                      // detail 에서만
  similarity?: number;                     // 시맨틱 검색 시
}

export interface AnalogSearchResult {
  mode: 'facet' | 'semantic';
  count: number;
  results: AnalogReport[];
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
  cancer_type: '암종',
  line_of_therapy: '치료차수',
  committee: '위원회',
  review_result: '심의결과',
  reimbursement_track: '등재트랙',
  coverage_gap_type: '허가↔급여 갭',
};

export function fetchAnalogFacets(): Promise<AnalogFacets> {
  return api.get<AnalogFacets>('/api/analog/facets');
}

export function searchAnalog(params: {
  filters?: Record<string, string>;
  fts?: string;
  semantic?: string;
  limit?: number;
}): Promise<AnalogSearchResult> {
  const q = new URLSearchParams();
  Object.entries(params.filters ?? {}).forEach(([k, v]) => { if (v) q.set(k, v); });
  if (params.fts) q.set('fts', params.fts);
  if (params.semantic) q.set('semantic', params.semantic);
  q.set('limit', String(params.limit ?? 50));
  return api.get<AnalogSearchResult>(`/api/analog/search?${q.toString()}`);
}

export function fetchAnalogDetail(id: number): Promise<AnalogReport> {
  return api.get<AnalogReport>(`/api/analog/report/${id}`);
}

export function generateAnalogBrief(reportIds: number[], query: string): Promise<AnalogBrief> {
  return api.post<AnalogBrief>('/api/analog/brief', { report_ids: reportIds, query });
}
