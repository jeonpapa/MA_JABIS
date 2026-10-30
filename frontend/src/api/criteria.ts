import { api } from './client';

// 급여 기준 (심평원 항암 공고 · 일반약제 고시 · KCD-9) — api/hira_criteria_routes.py

export interface SourceMeta {
  onco_consolidated: { title: string; effective_date: string | null; url: string | null } | null;
  onco_notice: { notice_no: string | null; effective_date: string | null; url: string | null } | null;
  kcd_master: { title: string; posted_date: string | null } | null;
  drug_booklet?: { title: string; posted_date: string | null; url: string | null } | null;
  drug_latest_effective: string | null;
  checked_at?: string | null;
}

export interface Marker { name: string; pos: boolean; label: string; value: string }

export interface OncoRow {
  kind: 'onco_regimen';
  key: string;
  cancer_code: string;
  cancer: string;
  setting_code: string;
  setting: string;
  line_code: string;
  line: string;
  regimen_no: string;
  regimen: string;
  target: string;
  markers: Marker[];
  biomarkers: string[];
  self_pay: string;
  subgroup: string;
  notice: string;
  effective: string;
  is_combo_table: boolean;
  added_condition: string;
  base_regimen?: string | null;
  footnotes?: { mark: string; text: string }[];
  source_url?: string | null;
  source_loc?: string;
  remark?: string;
  focus?: boolean;
  marker_match?: string[];
  keyword_hits?: string[];
}

export interface DrugItem {
  kind: 'drug_item';
  key: string;
  class_no: string;
  title: string;
  ingredient: string;
  criteria: string;
  excerpt: string | null;
  notice: string | null;
  effective: string | null;
  latest_basis: string | null;
  url: string | null;
  url_kind?: '고시' | '책자';
  kcd: { kcd_code: string; name_kr: string | null; review_status: string }[];
  match?: 'title' | 'mentioned' | 'same_ingredient';
  focus?: boolean;
  toc?: string[];
  age?: AgeClause | null;
}

export interface Age { lo: number; hi: number; months: number | null; label: string }
export interface AgeClause { status: 'match' | 'outside'; label?: string; text?: string; ranges?: string[]; message?: string }

export interface DrugSearchResult {
  resolved: { query: string; inns: string[]; via: string[] };
  onco: OncoRow[];
  general: DrugItem[];
  source: SourceMeta;
}

export interface Target { id: string; name: string; kind?: 'onco' | 'kcd' }

export interface Question {
  field: 'targets' | 'settings' | 'line' | 'markers' | 'none';
  text: string;
  options: { label: string; value: string | number | null }[];
}

export interface Filters {
  settings: string[];
  line: number | null;
  markers: string[];
}

export interface Facets {
  settings: { code: string; label: string; count: number }[];
  lines: { value: number; label: string }[];
  markers: { value: string; label: string; count: number }[];
  onco_rows: number;
}

export interface ClarifyResult {
  query: string;
  ai: boolean;
  ai_error?: string;
  interpretation: string;
  targets: Target[];
  alternatives: Target[];
  filters: Filters;
  drugs: { name: string; inns: string[] }[];
  keywords: string[];
  questions: Question[];
  summary: string;
  facets?: Facets;
  age?: Age | null;
}

export interface Answer {
  drug: string;
  status: 'covered' | 'combo_only' | 'none' | 'age_outside';
  message: string;
  elsewhere?: string[];
  rows: { key: string; regimen: string; cancer: string; setting_code: string; line_code: string; notice: string; effective: string; condition: string }[];
  items: { key: string; title: string; notice: string | null; effective: string | null; condition: string; age?: AgeClause | null }[];
}

export interface DiseaseResults {
  targets: Target[];
  onco: OncoRow[];
  combo: OncoRow[];
  general: (DrugItem & { relevance: number })[];
  mentioned?: { key: string; title: string; ingredient: string }[];
  age?: Age | null;
  answers: Answer[];
  matches: { key: string; regimen: string; cancer: string; setting_code: string; line_code: string; notice: string; effective: string; matched: string[]; combo?: boolean; condition: string }[];
  source: SourceMeta;
}

export type DiffSeg = { op: 'eq' | 'add' | 'del'; text: string };
export type CellStatus = 'base' | 'same' | 'changed' | 'only_this' | 'only_base' | 'none';

export interface Block { heading: string; status: 'same' | 'changed' | 'only_this' | 'only_base'; diff: DiffSeg[] | null; text?: string }

export interface CompareResult {
  base: number;
  focus: string | null;
  drugs: { query: string; label: string; inns: string[]; onco_count: number; general_count: number; bundle_only: boolean }[];
  coverage: { id: string; name: string; has: boolean[]; status: string[] }[];
  onco: {
    cancer: string; combo: boolean; setting: string; line: string; setting_code: string; line_code: string; differs: boolean;
    entries: { status: CellStatus; rows: (OncoRow & { diff: DiffSeg[] | null; compared_with?: string })[] }[];
  }[];
  general: (DrugItem & { blocks: Block[] | null; compared_with: string | null; status: 'base' | 'same' | 'changed' | 'shared' | 'only_this' })[][];
  focus_options: { id: string; name: string }[];
  highlights: string[];
  sources?: string[];
  line: number | null;
  summary: { cells: number; differ: number; diseases_differ: number };
  source: SourceMeta;
}

export const criteriaMeta = () =>
  api.get<{ source: SourceMeta; counts: Record<string, number> }>('/api/criteria/meta');
export const searchDrugCriteria = (q: string) =>
  api.get<DrugSearchResult>(`/api/criteria/drug/search?q=${encodeURIComponent(q)}`);
export const clarifyDisease = (query: string, answers: Record<string, unknown> = {}, targets?: string[]) =>
  api.post<ClarifyResult>('/api/criteria/disease/clarify', { query, answers, targets });
export const diseaseResults = (targets: string[], f: Filters & { drugs?: string[]; keywords?: string[]; age?: Age | null }) =>
  api.post<DiseaseResults>('/api/criteria/disease/results', { targets, ...f });
export const compareDrugs = (drugs: string[], base: number, focus?: string | null, line?: number | null) =>
  api.post<CompareResult>('/api/criteria/compare', { drugs, base, focus, line });
